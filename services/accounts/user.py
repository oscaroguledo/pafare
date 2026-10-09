from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, or_
from sqlalchemy.orm import selectinload
from typing import List, Optional, Dict, Any
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.accounts.user import User, Role, AccountStatus, get_role_id
from models.commerce.orders import Order
from core.exceptions import APIException
from schemas.accounts.user import Create as UserCreate
from services.accounts.email import queue, send_verification, send_password_reset
from core.utils.messages.email import email_language
from datetime import datetime, timedelta, timezone
import secrets
from core.utils.encryption import PasswordManager
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.password_manager = PasswordManager()
        
        # Search configuration
        self.similarity_threshold = 0.3
        self.weights = {
            "exact": 1.0,
            "prefix": 0.8,
            "fuzzy": 0.5
        }

    async def create(self, user_data: UserCreate, background_tasks: BackgroundTasks) -> User:
        """Create a new user with verification email"""
        passwordhash = self.password_manager.hash_password(
            user_data.password)
        verification_token = secrets.token_urlsafe(32)
        token_expiration = datetime.now(timezone.utc) + timedelta(hours=24)  # Token valid for 24 hours

        new_user = User(
            id=uuid7(),
            email=user_data.email,
            firstname=user_data.firstname,
            lastname=user_data.lastname,
            passwordhash=passwordhash,
            role_id=await get_role_id(self.db, user_data.role),
            phone=getattr(user_data, "phone", None),
            verification_token=verification_token,
            token_expiration=token_expiration,
            language=email_language(getattr(user_data, "language", None)),
        )
        self.db.add(new_user)
        await self.db.commit()
        await self.db.refresh(new_user)

        queue(background_tasks, send_verification, new_user.email, new_user.firstname, verification_token, new_user.language)

        return new_user

    async def verify(self, token: str, background_tasks: Optional[BackgroundTasks] = None):
        """Verify user email with token and send welcome email."""
        
        result = await self.db.execute(
            select(User).where(User.verification_token == token)
        )
        user = result.scalar_one_or_none()

        if not user:
            logger.warning(f"Email verification failed: No user found with token {token[:20]}...")
            raise APIException(
                status_code=400,
                message="Invalid or expired verification token",
            )

        if user.token_expiration < datetime.now(timezone.utc):
            logger.warning(f"Email verification failed: Token expired for user {user.email}. Token expired: {user.token_expiration}")
            raise APIException(
                status_code=400,
                message="Invalid or expired verification token",
            )

        user.verified = True
        user.verification_token = None
        user.token_expiration = None
        await self.db.commit()

        # User verification complete - no welcome email needed

    async def list(self, page: int = 1, limit: int = 10, role: Optional[str] = None, query: Optional[str] = None, status: Optional[str] = None) -> dict:
        """Get paginated list of users with order count"""
        offset = (page - 1) * limit

        # Build base query with order count using SQL aggregation
        base_query = (
            select(
                User,
                func.count(Order.id).label('order_count')
            )
            .outerjoin(Order, User.id == Order.user_id)
            .group_by(User.id)
        )

        # Apply role filter if provided
        if role:
            base_query = base_query.join(Role, User.role_id == Role.id).where(Role.name == role)

        # Apply status filter if provided
        if status:
            if status in {v.value for v in AccountStatus}:
                base_query = base_query.where(User.account_status == status)
            else:
                base_query = base_query.where(User.verified == (status == 'verified'))

        # Apply search query if provided
        search_condition = None
        if query:
            search_term = f"%{query}%"
            search_condition = or_(
                User.firstname.ilike(search_term),
                User.lastname.ilike(search_term),
                User.email.ilike(search_term),
            )
            base_query = base_query.where(search_condition)

        # Get total count
        count_query = select(func.count()).select_from(User)
        if role:
            count_query = count_query.join(Role, User.role_id == Role.id).where(Role.name == role)
        if status:
            if status in {v.value for v in AccountStatus}:
                count_query = count_query.where(User.account_status == status)
            else:
                count_query = count_query.where(User.verified == (status == 'verified'))
        if search_condition is not None:
            count_query = count_query.where(search_condition)
        count_result = await self.db.execute(count_query)
        total = count_result.scalar()

        # Apply pagination and ordering
        base_query = base_query.offset(offset).limit(limit).order_by(User.created_at.desc())
        result = await self.db.execute(base_query)
        rows = result.all()

        # Convert to list of dicts with user and order_count
        users_with_counts = []
        for row in rows:
            user = row[0]
            order_count = row[1]
            user_dict = user.to_dict()
            user_dict['order_count'] = order_count
            users_with_counts.append(user_dict)

        return {
            "users": users_with_counts,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }

    async def get(self, user_id: UUID) -> Optional[User]:
        """Get user by ID"""
        query = select(User).where(User.id == user_id).options(
            selectinload(User.addresses)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def update(self, user_id: UUID, user_data, allowed_fields: Optional[list] = None) -> Optional[User]:
        """Update user. Accepts either UserUpdate Pydantic model or plain dict.
        If allowed_fields provided, only those fields will be updated (for admin)."""
        query = select(User).where(User.id == user_id)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            return None

        # Handle both Pydantic models and plain dicts
        if isinstance(user_data, dict):
            data_dict = user_data
        else:
            data_dict = user_data.model_dump(exclude_unset=True)

        # Update fields
        for field, value in data_dict.items():
            if hasattr(user, field):
                if allowed_fields is None or field in allowed_fields:
                    setattr(user, field, value)

        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def search(
        self, 
        query: str, 
        limit: int = 20,
        role_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search users by name/email prefix; falls back to LIKE if pg_trgm is unavailable."""
        if not query or len(query.strip()) < 2:
            return []

        q = query.strip().lower()

        try:
            return await self._search_with_similarity(q, limit, role_filter)
        except Exception:
            # pg_trgm unavailable: fall back to LIKE. A failed raw-SQL statement leaves
            # the transaction aborted, so it must be rolled back first.
            await self.db.rollback()
            return await self._search_simple(q, limit, role_filter)

    async def _search_simple(
        self,
        query: str,
        limit: int,
        role_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Simple LIKE-based user search fallback."""
        search_term = f"%{query}%"
        stmt = (
            select(User)
            .where(User.account_status == AccountStatus.ACTIVE)
            .where(
                or_(
                    User.firstname.ilike(search_term),
                    User.lastname.ilike(search_term),
                    User.email.ilike(search_term),
                )
            )
            .limit(limit)
        )
        if role_filter:
            stmt = stmt.join(Role, User.role_id == Role.id).where(Role.name == role_filter)
        result = await self.db.execute(stmt)
        users = []
        for user in result.scalars().all():
            users.append({
                "id": str(user.id),
                "firstname": user.firstname,
                "lastname": user.lastname,
                "full_name": f"{user.firstname} {user.lastname}",
                "email": user.email,
                "role": user.role.value if hasattr(user.role, "value") else user.role,
                "verified": user.verified,
                "relevance_score": 1.0,
                "type": "user"
            })
        return users

    async def _search_with_similarity(
        self,
        query: str,
        limit: int = 20,
        role_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Advanced search using pg_trgm similarity."""
        # Build base conditions - account_status/verification_status are the real
        # columns; is_active/verified are Python-only properties on the model.
        base_conditions = ["u.account_status = 'active'"]
        params = {
            "query": query,
            "similarity_threshold": self.similarity_threshold,
            "limit": limit,
            "exact_weight": self.weights["exact"],
            "prefix_weight": self.weights["prefix"],
            "fuzzy_weight": self.weights["fuzzy"]
        }
        
        if role_filter:
            base_conditions.append("r.name = :role_filter")
            params["role_filter"] = role_filter
        
        where_clause = " AND ".join(base_conditions)
        
        sql_query = text(f"""
            SELECT 
                u.id,
                u.firstname,
                u.lastname,
                u.email,
                r.name as role,
                u.verified as verified,
                (
                    -- First name matching
                    CASE WHEN LOWER(u.firstname) = :query THEN :exact_weight
                         WHEN LOWER(u.firstname) LIKE CONCAT(:query, '%') THEN :prefix_weight
                         WHEN LOWER(u.firstname) LIKE CONCAT('%', :query, '%') THEN :prefix_weight * 0.7
                         ELSE similarity(LOWER(u.firstname), :query) * :fuzzy_weight
                    END +
                    -- Last name matching
                    CASE WHEN LOWER(u.lastname) = :query THEN :exact_weight
                         WHEN LOWER(u.lastname) LIKE CONCAT(:query, '%') THEN :prefix_weight
                         WHEN LOWER(u.lastname) LIKE CONCAT('%', :query, '%') THEN :prefix_weight * 0.7
                         ELSE similarity(LOWER(u.lastname), :query) * :fuzzy_weight
                    END +
                    -- Email matching (lower weight)
                    CASE WHEN LOWER(u.email) LIKE CONCAT(:query, '%') THEN :prefix_weight * 0.8
                         WHEN LOWER(u.email) LIKE CONCAT('%', :query, '%') THEN :prefix_weight * 0.6
                         ELSE similarity(LOWER(u.email), :query) * :fuzzy_weight * 0.8
                    END +
                    -- Full name matching (concatenated)
                    CASE WHEN LOWER(CONCAT(u.firstname, ' ', u.lastname)) LIKE CONCAT('%', :query, '%') THEN :prefix_weight * 0.9
                         ELSE similarity(LOWER(CONCAT(u.firstname, ' ', u.lastname)), :query) * :fuzzy_weight * 0.9
                    END
                ) as relevance_score
            FROM accounts.users u JOIN accounts.roles r ON r.id = u.role_id
            WHERE {where_clause}
            AND (
                LOWER(u.firstname) LIKE CONCAT('%', :query, '%')
                OR LOWER(u.lastname) LIKE CONCAT('%', :query, '%')
                OR LOWER(u.email) LIKE CONCAT('%', :query, '%')
                OR LOWER(CONCAT(u.firstname, ' ', u.lastname)) LIKE CONCAT('%', :query, '%')
                OR similarity(LOWER(u.firstname), :query) > :similarity_threshold
                OR similarity(LOWER(u.lastname), :query) > :similarity_threshold
                OR similarity(LOWER(u.email), :query) > :similarity_threshold
            )
            ORDER BY relevance_score DESC, verified DESC
            LIMIT :limit
        """)
        
        result = await self.db.execute(sql_query, params)
        
        users = []
        for row in result:
            users.append({
                "id": str(row.id),
                "firstname": row.firstname,
                "lastname": row.lastname,
                "full_name": f"{row.firstname} {row.lastname}",
                "email": row.email,
                "role": row.role,
                "verified": row.verified,
                "relevance_score": float(row.relevance_score),
                "type": "user"
            })
            
        return users

    # --- Admin user management methods ---

    async def verify_user_account(self, user_id: UUID) -> Optional[User]:
        """Verify user account (admin only)."""
        query = select(User).where(User.id == user_id)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            raise APIException(status_code=404, message="User not found")

        user.verified = True
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def reset_password(self, user_id: UUID) -> Dict[str, Any]:
        """Reset user password and send reset email (admin only)."""
        try:
            result = await self.db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if not user:
                raise APIException(status_code=404, message="User not found")

            # Generate reset token (stored in the same field the reset-confirmation flow reads from)
            reset_token = secrets.token_urlsafe(32)
            user.reset_token = reset_token
            user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=24)

            await self.db.commit()
            await self.db.refresh(user)

            await send_password_reset(user.email, user.firstname, reset_token, user.language, 24)

            return {
                "success": True,
                "message": f"Password reset email sent to {user.email}",
                "user_id": str(user.id),
                "email": user.email
            }

        except APIException:
            raise
        except Exception as e:
            logger.error(f"Password reset email failed for {user_id}: {e}")
            raise APIException(status_code=502, message="The reset link was created, but the email service didn't send it. Try again later.")

    async def deactivate(self, user_id: UUID) -> Dict[str, Any]:
        """Deactivate user account (admin only)."""
        try:
            result = await self.db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if not user:
                raise APIException(status_code=404, message="User not found")

            user.account_status = AccountStatus.INACTIVE
            await self.db.commit()
            await self.db.refresh(user)

            return {
                "success": True,
                "message": f"User {user.email} has been deactivated",
                "user_id": str(user.id),
                "email": user.email
            }

        except APIException:
            raise
        except Exception as e:
            raise APIException(
                status_code=500,
                message=f"Failed to deactivate user: {str(e)}"
            )

    async def activate(self, user_id: UUID) -> Dict[str, Any]:
        """Activate user account (admin only)."""
        try:
            result = await self.db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if not user:
                raise APIException(status_code=404, message="User not found")

            user.account_status = AccountStatus.ACTIVE
            await self.db.commit()
            await self.db.refresh(user)

            return {
                "success": True,
                "message": f"User {user.email} has been activated",
                "user_id": str(user.id),
                "email": user.email
            }

        except APIException:
            raise
        except Exception as e:
            raise APIException(
                status_code=500,
                message=f"Failed to activate user: {str(e)}"
            )

    async def update_role(self, user_id: UUID, new_role: str) -> Optional[User]:
        """Update user role (admin only)."""
        query = select(User).where(User.id == user_id)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            return None

        user.role_id = await get_role_id(self.db, new_role)
        await self.db.commit()
        await self.db.refresh(user)
        return user
