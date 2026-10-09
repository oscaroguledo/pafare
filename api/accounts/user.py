from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.dependencies import require_permission, require_self_or_permission
from core.exceptions import APIException
from core.scopes import Scope
from core.utils.response import Response
from models.accounts.user import User, AccountStatus
from schemas.accounts.user import (
    UserCreate, UserUpdate, UserList, UserGet, UserResponse, RoleChange, EmailChange,
)
from services.accounts.user import UserService

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_permission(Scope.USERS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a user with any role (needs users:create). Public sign-up is POST /auth/register/."""
    user = await UserService(db).create(payload, background_tasks)
    return Response.success(
        data=UserResponse.model_validate(user), message="User created", status_code=status.HTTP_201_CREATED
    )


@router.get("/")
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None, description="Matches first, middle or last name, or email"),
    role: Optional[str] = Query(None, description="Role name"),
    account_status: Optional[AccountStatus] = Query(None, alias="status"),
    country: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    current_user: User = Depends(require_permission(Scope.USERS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """List users, newest first (needs users:view)."""
    filters = UserList(
        search=search, role_name=role, status=account_status, country=country, language=language, gender=gender
    )
    result = await UserService(db).list(filters, page=page, limit=limit)
    users = [UserResponse.model_validate(user) for user in result["users"]]
    return Response.success(data=users, pagination=result["pagination"])


@router.get("/{user_id}/")
async def get_user(
    user_id: UUID,
    current_user: User = Depends(require_self_or_permission(Scope.USERS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Get a user. You can always read yourself; others need users:view."""
    user = await UserService(db).get(UserGet(id=user_id))
    return Response.success(data=UserResponse.model_validate(user))


@router.patch("/{user_id}/")
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    current_user: User = Depends(require_self_or_permission(Scope.USERS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Update profile fields. You can always edit yourself; others need users:edit."""
    user = await UserService(db).update(user_id, payload)
    return Response.success(data=UserResponse.model_validate(user), message="User updated")


@router.put("/{user_id}/role/")
async def change_role(
    user_id: UUID,
    payload: RoleChange,
    current_user: User = Depends(require_permission(Scope.ROLES_ASSIGN)),
    db: AsyncSession = Depends(get_db),
):
    """Give a user a different role (needs roles:assign). Nobody changes their own role."""
    if user_id == current_user.id:
        raise APIException(status_code=400, message="You can't change your own role")
    user = await UserService(db).change_role(user_id, payload.role_id)
    return Response.success(data=UserResponse.model_validate(user), message="Role updated")


@router.put("/{user_id}/email/")
async def change_email(
    user_id: UUID,
    payload: EmailChange,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_permission(Scope.USERS_CHANGE_EMAIL)),
    db: AsyncSession = Depends(get_db),
):
    """Change a user's email (needs users:change_email). The new address must be verified again."""
    user = await UserService(db).change_email(user_id, payload.new_email, background_tasks)
    return Response.success(data=UserResponse.model_validate(user), message="Email updated; verification needed")


@router.post("/{user_id}/reset-password/")
async def reset_user_password(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_RESET_PASSWORD)),
    db: AsyncSession = Depends(get_db),
):
    """Send a user a password-reset link (needs users:reset_password)."""
    await UserService(db).reset_password(user_id)
    return Response.success(message="Password reset link sent")


@router.post("/{user_id}/deactivate/")
async def deactivate_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_DEACTIVATE)),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate an account (needs users:deactivate). You can't deactivate yourself."""
    if user_id == current_user.id:
        raise APIException(status_code=400, message="You can't deactivate your own account")
    await UserService(db).deactivate(user_id)
    return Response.success(message="User deactivated")


@router.post("/{user_id}/activate/")
async def activate_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_ACTIVATE)),
    db: AsyncSession = Depends(get_db),
):
    """Activate an account (needs users:activate)."""
    await UserService(db).activate(user_id)
    return Response.success(message="User activated")


@router.post("/{user_id}/suspend/")
async def suspend_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_SUSPEND)),
    db: AsyncSession = Depends(get_db),
):
    """Suspend an account (needs users:suspend). You can't suspend yourself."""
    if user_id == current_user.id:
        raise APIException(status_code=400, message="You can't suspend your own account")
    await UserService(db).suspend(user_id)
    return Response.success(message="User suspended")


@router.post("/{user_id}/unsuspend/")
async def unsuspend_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_UNSUSPEND)),
    db: AsyncSession = Depends(get_db),
):
    """Lift a suspension (needs users:unsuspend). The account becomes active again."""
    await UserService(db).unsuspend(user_id)
    return Response.success(message="User unsuspended")
