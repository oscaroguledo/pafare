"""Shared FastAPI dependencies used across multiple routers."""
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from core.db import get_db
from core.exceptions import APIException
from core.logging import get_structured_logger
from models.accounts.user import User
from services.accounts.auth import AuthService

logger = get_structured_logger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login", auto_error=False)


async def get_current_auth_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Authentication - returns None if not authenticated"""
    if not token:
        return None
    try:
        auth_service = AuthService(db)
        return await auth_service.current_user(token)
    except HTTPException:
        # Expected for a missing/expired/invalid token - treat as unauthenticated
        return None
    except Exception:
        # Unexpected failure (DB error, bug, etc.) - log it instead of masking it as "not authenticated"
        logger.error("Unexpected error while resolving current user from token", exc_info=True)
        return None


async def require_auth(
    current_user: Optional[User] = Depends(get_current_auth_user),
) -> User:
    """Raises 401 if user is not authenticated."""
    if current_user is None:
        raise APIException(status_code=401, message="Authentication required")
    return current_user


def require_admin(current_user: Optional[User] = Depends(get_current_auth_user)) -> User:
    if current_user is None:
        raise APIException(status_code=401, message="Authentication required")
    if not current_user.is_staff:  # superadmin, admin or manager
        raise APIException(status_code=403, message="Admin access required")
    return current_user

