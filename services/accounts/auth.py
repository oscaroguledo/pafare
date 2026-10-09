from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from fastapi import HTTPException, status, BackgroundTasks

import jwt
from jwt import PyJWTError as JWTError
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import secrets
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from core.config import settings
from models.accounts.user import User, UserRole
from models.accounts.tokens import RevokedToken
from schemas.accounts.user import Create as UserCreate, Response as UserResponse
from schemas.accounts.auth import Auth as AuthResponse
from services.accounts.user import UserService
from services.accounts.email import queue, send_password_reset
from core.utils.encryption import PasswordManager

logger = get_structured_logger(__name__)

# How long a replaced refresh token keeps working, for requests already in flight (e.g. two tabs refreshing at once).
REFRESH_GRACE_SECONDS = 60

MAX_FAILED_LOGIN_ATTEMPTS = 5
ACCOUNT_LOCKOUT_MINUTES = 15


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.password_manager = PasswordManager()

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return self.password_manager.verify_password(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        """Hash a password."""
        return self.password_manager.hash_password(password)

    def make_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create a JWT access token."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "type": "access",
            "jti": str(uuid7())  # JWT ID for token tracking
        })
        
        encoded_jwt = jwt.encode(
            to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return encoded_jwt

    async def make_refresh_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create a JWT refresh token."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "type": "refresh",
            "jti": str(uuid7())  # JWT ID for token tracking
        })
        
        encoded_jwt = jwt.encode(
            to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        
        return encoded_jwt



    def verify_token(self, token: str, token_type: str = "access") -> Dict[str, Any]:
        """Verify and decode JWT token."""
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            
            # Check token type
            if payload.get("type") != token_type:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token type. Expected {token_type}",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Check expiration
            exp = payload.get("exp")
            if exp is None or datetime.now(timezone.utc) > datetime.fromtimestamp(exp, tz=timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            return payload
            
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def refresh_token(self, refresh_token: str) -> Dict[str, str]:
        """Generate new access token using refresh token."""
        try:
            # Verify refresh token
            payload = self.verify_token(refresh_token, "refresh")
            jti = payload.get("jti")
            user_id_from_token = payload.get("sub")

            if not jti or not user_id_from_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token payload"
                )
            if await self.is_revoked(jti):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has been revoked")
            
            # Get user from token
            user = await self.get(user_id=user_id_from_token)
            if not user or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or inactive"
                )
            
            token_data = {
                "sub": str(user.id),
                "email": user.email,
                "is_active": user.is_active,
                "role": getattr(user, 'role', 'user')
            }
            # Every refresh hands out a fresh refresh token, so a session ends only after
            # REFRESH_TOKEN_EXPIRE_DAYS without use. The old one is retired after a short grace period.
            await self._retire_refresh_token(jti, payload["exp"])
            return {
                "access_token": self.make_access_token(token_data),
                "refresh_token": await self.make_refresh_token(token_data),
                "token_type": "bearer"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not refresh token"
            )

    async def revoke_token(self, token: str, token_type: str = "refresh") -> bool:
        """Revoke a token by its jti until it expires; invalid or expired tokens return False."""
        try:
            payload = self.verify_token(token, token_type)
        except HTTPException:
            return False
        jti, exp = payload.get("jti"), payload.get("exp")
        if not jti or not exp:
            return False
        now = datetime.now(timezone.utc)
        await self.db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
        existing = await self.db.get(RevokedToken, jti)
        if existing:
            existing.usable_until = None  # a replaced token still in its grace period ends now
        else:
            self.db.add(RevokedToken(jti=jti, expires_at=datetime.fromtimestamp(exp, tz=timezone.utc)))
        await self.db.commit()
        return True

    async def _retire_refresh_token(self, jti: str, exp: int) -> None:
        """Mark a replaced refresh token revoked after REFRESH_GRACE_SECONDS (kept if already retired or revoked)."""
        if not await self.db.get(RevokedToken, jti):
            self.db.add(RevokedToken(
                jti=jti,
                expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
                usable_until=datetime.now(timezone.utc) + timedelta(seconds=REFRESH_GRACE_SECONDS),
            ))
            await self.db.commit()

    async def is_revoked(self, jti: Optional[str]) -> bool:
        if not jti:
            return False
        row = await self.db.get(RevokedToken, jti)
        return row is not None and (row.usable_until is None or row.usable_until <= datetime.now(timezone.utc))

    async def get(self, user_id: Optional[str] = None, email: Optional[str] = None) -> Optional[User]:
        """Get user by ID or email."""
        if user_id:
            result = await self.db.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()
        elif email:
            result = await self.db.execute(select(User).where(User.email == email))
            return result.scalar_one_or_none()
        return None

    
    async def create(self, user_data: UserCreate, background_tasks: BackgroundTasks) -> UserResponse:
        """Create a new user."""
        # Check if user already exists
        existing_user = await self.get(email=user_data.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

        # Self-registration is always a customer; roles are only granted by admins.
        user_data = user_data.model_copy(update={"role": UserRole.CUSTOMER})
        user_service = UserService(self.db)
        new_user = await user_service.create(user_data, background_tasks)

        return UserResponse.model_validate(new_user)

    async def authenticate(self, email: str, password: str, background_tasks: BackgroundTasks) -> AuthResponse:
        """Authenticate user and return JWT tokens."""
        user = await self.get(email=email)
        if not user:
            logger.info(f"Login failed: no account for {email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        now = datetime.now(timezone.utc)
        if user.locked_until and user.locked_until > now:
            retry_after_minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
            logger.warning(f"Login blocked for {email}: account locked for {retry_after_minutes} more minute(s)")
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account temporarily locked due to too many failed login attempts. Try again in {retry_after_minutes} minute(s).",
            )

        password_verified = self.verify_password(password, user.passwordhash)

        if not password_verified:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=ACCOUNT_LOCKOUT_MINUTES)
                logger.warning(
                    f"Account locked for {email} after {user.failed_login_attempts} failed login attempts"
                )
            self.db.add(user)
            await self.db.commit()
            logger.info(f"Login failed: incorrect password for {email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check if user is active
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is deactivated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Successful login - reset failed-attempt tracking and update last_login
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login = func.now()
        self.db.add(user)
        await self.db.commit()

        logger.info(f"Login successful for {email}")
        return await self.issue_tokens(user)

    async def issue_tokens(self, user: User) -> AuthResponse:
        """Access and refresh tokens for a signed-in user (password, social or passkey sign-in)."""
        # Database-set fields (e.g. updated_at) are stale after a commit; load them before building the response.
        await self.db.refresh(user)
        token_data = {"sub": str(user.id), "email": user.email, "role": user.role, "is_active": user.is_active}
        return AuthResponse(
            access_token=self.make_access_token(token_data),
            token_type="bearer",
            refresh_token=await self.make_refresh_token(token_data),
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=UserResponse.model_validate(user),
        )

    async def current_user(
        self,
        token: str
    ) -> User:
        """Get current authenticated user using enhanced JWT verification."""
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

        try:
            # Decode and verify token
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            
            # Check token type
            token_type = payload.get("type")
            if token_type != "access":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            

            # Get user ID from token
            user_id: str = payload.get("sub")
            if user_id is None:
                raise credentials_exception
                
            # Check token expiration
            exp = payload.get("exp")
            if exp is None or datetime.now(timezone.utc) > datetime.fromtimestamp(exp, tz=timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired",
                    headers={"WWW-Authenticate": "Bearer"},
                )
                
        except JWTError:
            raise credentials_exception

        if await self.is_revoked(payload.get("jti")):
            raise credentials_exception

        # Get user from database
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if user is None:
            raise credentials_exception
            
        # Check if user is still active
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is deactivated",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        return user



    async def send_reset(self, email: str, background_tasks: BackgroundTasks):
        """Send password reset email"""
        user = await self.get(email=email)
        if not user:
            # Return silently for security (don't reveal if email exists)
            logger.info(f"Password reset requested for non-existent email: {email}")
            return
        
        # Generate reset token (32 bytes = 43 characters in base64)
        reset_token = secrets.token_urlsafe(32)
        
        # Store token in user record with expiration (1 hour)
        user.reset_token = reset_token
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await self.db.commit()
        
        queue(background_tasks, send_password_reset, user.email, user.firstname, reset_token, user.language, 1)
        logger.info(f"Password reset email queued for {user.email}")

    async def reset_pwd(self, token: str, new_password: str):
        """Reset password using reset token"""
        # Find user by reset token
        result = await self.db.execute(
            select(User).where(User.reset_token == token)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            logger.warning("Password reset attempted with invalid token")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token"
            )
        
        # Check token expiration
        if not user.reset_token_expires or user.reset_token_expires < datetime.now(timezone.utc):
            logger.warning(f"Password reset attempted with expired token for user {user.email}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired. Please request a new password reset link."
            )
        
        # Update password and clear reset token
        user.passwordhash = self.get_password_hash(new_password)
        user.reset_token = None
        user.reset_token_expires = None
        await self.db.commit()
        
        logger.info(f"Password reset successfully for user {user.email}")
