
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import IntegrityError
import time
from typing import Dict
from uuid import UUID
from datetime import datetime, timezone
from core.utils.uuid_utils import uuid7
from models.accounts.user import User, AccountStatus
from models.accounts.role import Role
from core.exceptions import APIException
from schemas.accounts.user import UserCreate, UserGet, UserList, UserLogin, UserUpdate
from core.logging import get_structured_logger
from core.utils.encryption import PasswordManager, JWTManager, TokenError
from core.utils.messages.email import EmailService
from services.accounts.role import RoleService

logger = get_structured_logger(__name__)

# user id -> when a fresh verification link was last sent because they opened an expired one. Kept per process, so with
# several workers a user can get one per worker; it exists to stop an old expired link being used to flood an inbox.
_recent_resends: Dict[UUID, float] = {}


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.password_manager = PasswordManager()
        self.jwt_manager = JWTManager()
        self.role_service = RoleService(db)  # Assuming you have a RoleService to fetch role_id by name


    async def create(self, user_data: UserCreate, background_tasks: BackgroundTasks) -> User:
        """Create a new user with verification email.
        Raises 400 if the role is missing or unknown, 409 if the email is already registered."""
        if not user_data.role_name:
            raise APIException(status_code=400, message="role_name is required")
        role = await self.role_service.get_by_name(user_data.role_name)
        if not role:
            raise APIException(status_code=400, message=f"Role '{user_data.role_name}' not found")
        email = user_data.email.lower()
        if (await self.db.execute(select(User.id).where(User.email == email))).first():
            raise APIException(status_code=409, message="A user with this email already exists")
        passwordhash = self.password_manager.hash_password(user_data.password)
        new_user = User(
            id=uuid7(),
            email=email,
            firstname=user_data.firstname.lower() if user_data.firstname else "",
            middlename=user_data.middlename.lower() if user_data.middlename else "",
            lastname=user_data.lastname.lower() if user_data.lastname else "",
            passwordhash=passwordhash,
            account_status=user_data.account_status,
            phone=user_data.phone,
            role_id=role.id,
            language=user_data.language,
            timezone=user_data.timezone,
            country=user_data.country,
            avatar_url=user_data.avatar_url,
            dob=user_data.dob,
            gender=user_data.gender,
        )
        self.db.add(new_user)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two sign-ups for the same email raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message="A user with this email already exists")
        await self.db.refresh(new_user)

        # Stateless link token: signed and expiring, so no token column is needed
        verification_token = self.jwt_manager.create_verification_token({"sub": str(new_user.id), "email": new_user.email})
        EmailService.queue(
            background_tasks, EmailService.send_verification, new_user.email, new_user.firstname.title(), verification_token,
            new_user.language, self.jwt_manager.verification_ttl.total_seconds() / 3600,
        )

        new_user.role_name = role.name
        return new_user

    async def login(self, user_login: UserLogin) -> Dict[str, str]:
        """Check the email and password and return a stateless access/refresh token pair.
        Raises 401 for an unknown email or wrong password, 403 if the account is suspended or not yet verified."""
        result = await self.db.execute(
            select(User).where(User.email == user_login.email.lower())
        )
        user = result.scalar_one_or_none()

        # One message for an unknown email and a wrong password, so nothing leaks
        if not user or not self.password_manager.verify_password(user_login.password, user.passwordhash):
            raise APIException(status_code=401, message="Invalid credentials")
        # Past this point the password was right, so it is safe to say why the login is refused
        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account suspended. Contact support.")
        if user.account_status == AccountStatus.INACTIVE:
            raise APIException(status_code=403, message="Account not active. Verify your email first.")
        role = await self.role_service.get_by_id(user.role_id)
        if not role:
            raise APIException(status_code=500, message="User has no valid role")
        user.last_login = datetime.now(timezone.utc)
        await self.db.commit()

        return self.jwt_manager.create_token_pair({
            "sub": str(user.id),
            "email": user.email,
            "account_status": user.account_status,
            "role": role.name,
        })

    async def verify(self, token: str, background_tasks: BackgroundTasks) -> bool:
        """Mark the user's email verified from a verification-link token.
        If the link has expired, a fresh one is sent to the same address, and a 400 says so.
        Raises 400 if the token is invalid, of the wrong type, or no longer matches the user."""
        try:
            payload = self.jwt_manager.decode(token, JWTManager.VERIFICATION)
        except TokenError as e:
            if not e.expired:
                logger.warning(f"Email verification failed: {e}")
                raise APIException(status_code=400, message="Invalid or expired verification token")
            # Expired but correctly signed: read who it was for and send them a new link
            payload = self.jwt_manager.decode(token, JWTManager.VERIFICATION, verify_exp=False)
            user = (await self.db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
            
            if user and user.email == payload.get("email") and user.account_status == AccountStatus.INACTIVE:
                # One fresh link per user per link lifetime, so an old link can't be used to flood their inbox
                ttl = self.jwt_manager.verification_ttl.total_seconds()
                now = time.monotonic()
                for stale in [uid for uid, sent in _recent_resends.items() if now - sent >= ttl]:
                    del _recent_resends[stale]
                if user.id in _recent_resends:
                    raise APIException(status_code=429, message="A new verification link was already sent. Check your email, or try again in a few minutes.")
                _recent_resends[user.id] = now
                verification_token = self.jwt_manager.create_verification_token({"sub": str(user.id), "email": user.email})
                # Sent now, not queued: background tasks only run after a normal response, and this one ends in an error
                await EmailService.send_verification(
                    user.email, user.firstname.title(), verification_token, user.language, ttl / 3600
                )
                raise APIException(status_code=400, message="This verification link has expired. A new one has been sent to your email.")
            
            raise APIException(status_code=400, message="Invalid or expired verification token")

        user = (await self.db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
        # The token was issued for this address; refuse it if the email has changed since
        if not user or user.email != payload.get("email"):
            raise APIException(status_code=400, message="Invalid or expired verification token")

        if user.account_status != AccountStatus.SUSPENDED:
            user.account_status = AccountStatus.ACTIVE
            await self.db.commit()
            await self.db.refresh(user)
        return True
    
    async def get(self, user_get: UserGet) -> User:
        """Get a user by id, email, or both (which must then match the same user) as a User with `role_name` set.
        Raises 404 if there is no match."""
        conditions = []
        if user_get.id is not None:
            conditions.append(User.id == user_get.id)
        if user_get.email is not None:
            conditions.append(User.email == user_get.email.lower())
        row = (await self.db.execute(
            select(User, Role.name).join(Role, User.role_id == Role.id).where(and_(*conditions))
        )).first()
        if not row:
            raise APIException(status_code=404, message="User not found")
        user, role_name = row
        user.role_name = role_name
        return user

    async def list(self, filter: UserList, page: int = 1, limit: int = 10) -> dict:
        """Get a paginated, filtered list of users, newest first.
        Raises 400 if page is below 1 or limit is outside 1-100."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")
        conditions = []
        if filter.role_name:
            conditions.append(Role.name == filter.role_name.lower())
        # `status` and `account_status` are the same filter; an explicit `status` wins over the default
        account_status = filter.status or filter.account_status
        if account_status:
            conditions.append(User.account_status == account_status.value)
        if filter.firstname:
            conditions.append(User.firstname.ilike(f"%{filter.firstname}%"))
        if filter.middlename:
            conditions.append(User.middlename.ilike(f"%{filter.middlename}%"))
        if filter.lastname:
            conditions.append(User.lastname.ilike(f"%{filter.lastname}%"))
        if filter.phone:
            conditions.append(User.phone.ilike(f"%{filter.phone}%"))
        if filter.country:
            conditions.append(User.country.ilike(f"%{filter.country}%"))
        if filter.language:
            conditions.append(func.lower(User.language) == filter.language.lower())
        if filter.dob:
            conditions.append(User.dob == filter.dob)
        if filter.gender:
            conditions.append(func.lower(User.gender) == filter.gender.lower())
        if filter.search:
            term = f"%{filter.search}%"
            conditions.append(or_(
                User.firstname.ilike(term),
                User.middlename.ilike(term),
                User.lastname.ilike(term),
                User.email.ilike(term),
            ))

        total = (await self.db.execute(
            select(func.count()).select_from(User).join(Role, User.role_id == Role.id).where(*conditions)
        )).scalar() or 0

        rows = (await self.db.execute(
            select(User, Role.name)
            .join(Role, User.role_id == Role.id)
            .where(*conditions)
            .order_by(User.created_at.desc(), User.id.desc())  # id breaks ties so pages never repeat or skip
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

        for user, role_name in rows:
            user.role_name = role_name  # plain attribute (not a column) so the route can build a UserResponse

        return {
            "users": [user for user, _ in rows],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def update(self, user_id: UUID, user_update: UserUpdate) -> User:
        """Update the profile fields that were sent (PATCH semantics) and return the user.
        Raises 404 if the user doesn't exist, 400 if nothing was sent or a required field is set to null."""
        row = (await self.db.execute(
            select(User, Role.name).join(Role, User.role_id == Role.id).where(User.id == user_id)
        )).first()
        if not row:
            raise APIException(status_code=404, message="User not found")
        user, role_name = row
        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account suspended. Contact support.")
        changes = user_update.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")

        required = {"firstname", "lastname", "language"}  # NOT NULL columns
        for field, value in changes.items():
            if value is None and field in required:
                raise APIException(status_code=400, message=f"{field} cannot be null")
            if field in {"firstname", "middlename", "lastname"}:
                value = value.strip().lower() if value else ("" if field == "middlename" else value)
            setattr(user, field, value)

        await self.db.commit()
        await self.db.refresh(user)
        user.role_name = role_name
        return user

    async def change_role(self, user_id: UUID, new_role_id: UUID) -> User:
        """Change the role of a user (admin only).
        Raises 404 if the user or the role doesn't exist."""
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=404, message="User not found")
        
        role = await self.role_service.get_by_id(new_role_id)
        if not role:
            raise APIException(status_code=404, message="Role not found")

        user.role_id = role.id
        await self.db.commit()
        await self.db.refresh(user)

        user.role_name = role.name
        return user

    async def change_email(self, user_id: UUID, new_email: str, background_tasks: BackgroundTasks) -> User:
        """Change the email of a user (admin only). The new address is unverified, so the account goes back to
        inactive (a suspended account stays suspended) and a verification link is issued for it.
        Raises 404 if the user doesn't exist, 409 if the email is already taken."""
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=404, message="User not found")

        new_email = new_email.lower()
        if (await self.db.execute(select(User.id).where(User.email == new_email, User.id != user_id))).first():
            raise APIException(status_code=409, message="A user with this email already exists")

        role = await self.role_service.get_by_id(user.role_id)
        if not role:
            raise APIException(status_code=500, message="User has no valid role")

        user.email = new_email
        if user.account_status != AccountStatus.SUSPENDED:
            user.account_status = AccountStatus.INACTIVE
        await self.db.commit()
        await self.db.refresh(user)

        verification_token = self.jwt_manager.create_verification_token({"sub": str(user.id), "email": user.email})
        EmailService.queue(
            background_tasks, EmailService.send_verification, user.email, user.firstname.title(), verification_token,
            user.language, self.jwt_manager.verification_ttl.total_seconds() / 3600,
        )

        user.role_name = role.name
        return user

    async def reset_password(self, user_id: UUID, background_tasks: BackgroundTasks) -> bool:
        """Start a password reset: build a signed 15-minute link token for the user and email it to them.
        Raises 404 if the user doesn't exist."""
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=404, message="User not found")
        
        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account suspended. Contact support.")
        
        reset_token = self.jwt_manager.create_password_reset_token({"sub": str(user.id)})
        EmailService.queue(
            background_tasks, EmailService.send_password_reset, user.email, user.firstname.title(), reset_token,
            user.language, self.jwt_manager.reset_ttl.total_seconds() / 3600,
        )

        return True

    async def set_password(self, token: str, new_password: str) -> bool:
        """Finish a password reset from the emailed token.
        Raises 400 if the token is invalid, expired, or the user is gone.
        The caller validates password strength."""
        try:
            payload = self.jwt_manager.decode(token, JWTManager.PASSWORD_RESET)
        except TokenError:
            raise APIException(status_code=400, message="Invalid or expired reset token")

        user = (await self.db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=400, message="Invalid or expired reset token")

        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account suspended. Contact support.")
        user.passwordhash = self.password_manager.hash_password(new_password)
        await self.db.commit()
        return True

    async def deactivate(self, user_id: UUID) -> bool:
        """Deactivate a user account (admin only). Raises 404 if the user doesn't exist."""
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=404, message="User not found")
        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account suspended. Contact support.")
        user.account_status = AccountStatus.INACTIVE
        await self.db.commit()

        return True

    async def activate(self, user_id: UUID) -> bool:
        """Activate a user account (admin only). Raises 404 if the user doesn't exist."""
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=404, message="User not found")
        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account suspended. Contact support.")
        user.account_status = AccountStatus.ACTIVE
        await self.db.commit()

        return True

    async def suspend(self, user_id: UUID) -> bool:
        """Suspend a user account (admin only). Raises 404 if the user doesn't exist."""
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=404, message="User not found")
        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account already suspended.")
        user.account_status = AccountStatus.SUSPENDED
        await self.db.commit()

        return True

    async def refresh_token(self, refresh_token: str) -> Dict[str, str]:
        """Exchange a valid refresh token for a new access/refresh pair.
        Raises 401 if the token is invalid or expired, or the user is gone or inactive."""
        try:
            payload = self.jwt_manager.decode(refresh_token, JWTManager.REFRESH)
        except TokenError as e:
            raise APIException(status_code=401, message=str(e))

        user = (await self.db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=401, message="User not found or inactive")
        if user.account_status == AccountStatus.SUSPENDED:
            raise APIException(status_code=403, message="Account suspended. Contact support.")
        if user.account_status != AccountStatus.ACTIVE:
            raise APIException(status_code=401, message="User not found or inactive")

        role = await self.role_service.get_by_id(user.role_id)
        if not role:
            raise APIException(status_code=500, message="User has no valid role")

        # Rebuilt from the database, so a role change or deactivation takes effect at the next refresh
        return self.jwt_manager.create_token_pair({
            "sub": str(user.id),
            "email": user.email,
            "account_status": user.account_status,
            "role": role.name,
        })

    async def unsuspend(self, user_id: UUID) -> bool:
        """Lift a suspension: the account becomes active again (admin only).
        Raises 404 if the user doesn't exist, 400 if the account isn't suspended."""
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            raise APIException(status_code=404, message="User not found")
        if user.account_status != AccountStatus.SUSPENDED:
            raise APIException(status_code=400, message="Account is not suspended")
        user.account_status = AccountStatus.ACTIVE
        await self.db.commit()

        return True
