"""Shared FastAPI dependencies: who is calling, and what they are allowed to do."""
from typing import Optional
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.exceptions import APIException
from core.utils.encryption import JWTManager, TokenError
from models.accounts.user import User
from schemas.accounts.user import UserGet
from services.accounts.permission import PermissionService
from services.accounts.user import UserService

bearer_scheme = HTTPBearer(auto_error=False)
jwt_manager = JWTManager()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """The signed-in user from the access token. Raises 401 if there is no valid token or the user is gone,
    403 if the account is not active."""
    if credentials is None:
        raise APIException(status_code=401, message="Authentication required")
    try:
        payload = jwt_manager.decode(credentials.credentials)
    except TokenError as e:
        raise APIException(status_code=401, message=str(e))

    try:
        user = await UserService(db).get(UserGet(id=payload["sub"]))
    except APIException as e:
        if e.status_code == 404:
            raise APIException(status_code=401, message="User no longer exists")
        raise
    if not user.is_active:
        raise APIException(status_code=403, message="Account is not active")
    return user


def require_permission(scope: str):
    """Dependency factory: the caller's role must have `scope` (e.g. 'users:view'). Returns the user."""
    async def checker(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if not await PermissionService(db).has_permission(user.role_id, scope):
            raise APIException(status_code=403, message=f"Missing permission: {scope}")
        return user
    return checker


def require_self_or_permission(scope: str, param: str = "user_id"):
    """Like require_permission, but a caller may also act on their own record (the `user_id` path parameter)."""
    async def checker(
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if str(user.id) == request.path_params.get(param):
            return user
        if not await PermissionService(db).has_permission(user.role_id, scope):
            raise APIException(status_code=403, message=f"Missing permission: {scope}")
        return user
    return checker


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """The signed-in user, or None when no token was sent. A token that is sent must still be valid."""
    if credentials is None:
        return None
    return await get_current_user(credentials, db)


def owner_unless_permitted(scope: str):
    """Dependency factory for records that belong to a guest (bookings, reviews). Returns None when the caller
    has `scope` (they may act on anyone's), otherwise the caller's own id, to pass to a service as `user_id`
    so it only touches their records."""
    async def checker(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Optional[UUID]:
        if await PermissionService(db).has_permission(user.role_id, scope):
            return None
        return user.id
    return checker
