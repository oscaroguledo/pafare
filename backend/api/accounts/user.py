from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.docs import errors
from core.db import get_db
from core.dependencies import require_permission, require_self_or_permission
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.scopes import Scope
from core.utils.response import Response
from models.accounts.user import User, AccountStatus
from schemas.accounts.user import (
    UserCreate, UserUpdate, UserList, UserGet, UserResponse, RoleChange, EmailChange,
)
from services.accounts.user import UserService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


@router.post(
    "/", status_code=status.HTTP_201_CREATED, summary="Create a user",
    responses=errors(400, 401, 403, 409, 422, 500),
)
async def create_user(
    payload: UserCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_permission(Scope.USERS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a user with any role (needs users:create). Public sign-up is POST /auth/register/."""
    try:
        user = await UserService(db).create(payload, background_tasks)
        logger.info(f"{current_user.email} created user {user.email} with role {user.role_name}")
        return Response.success(
            data=UserResponse.model_validate(user), message="User created", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a user failed")
        raise APIException(status_code=500, message="Could not create the user")


@router.get("/", summary="List users", responses=errors(400, 401, 403, 422, 500))
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
    try:
        filters = UserList(
            search=search, role_name=role, status=account_status, country=country, language=language, gender=gender
        )
        result = await UserService(db).list(filters, page=page, limit=limit)
        users = [UserResponse.model_validate(user) for user in result["users"]]
        return Response.success(data=users, pagination=result["pagination"])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing users failed")
        raise APIException(status_code=500, message="Could not load the users")


@router.get("/{user_id}/", summary="Get a user", responses=errors(401, 403, 404, 500))
async def get_user(
    user_id: UUID,
    current_user: User = Depends(require_self_or_permission(Scope.USERS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """Get a user. You can always read yourself; others need users:view."""
    try:
        user = await UserService(db).get(UserGet(id=user_id))
        return Response.success(data=UserResponse.model_validate(user))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching user {user_id} failed")
        raise APIException(status_code=500, message="Could not load the user")


@router.patch("/{user_id}/", summary="Update a profile", responses=errors(400, 401, 403, 404, 422, 500))
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    current_user: User = Depends(require_self_or_permission(Scope.USERS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Update profile fields (only the ones sent change). You can always edit yourself; others need users:edit."""
    try:
        user = await UserService(db).update(user_id, payload)
        if user_id != current_user.id:
            logger.info(f"{current_user.email} edited the profile of {user.email}")
        return Response.success(data=UserResponse.model_validate(user), message="User updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating user {user_id} failed")
        raise APIException(status_code=500, message="Could not update the user")


@router.put("/{user_id}/role/", summary="Change a user's role", responses=errors(400, 401, 403, 404, 422, 500))
async def change_role(
    user_id: UUID,
    payload: RoleChange,
    current_user: User = Depends(require_permission(Scope.ROLES_ASSIGN)),
    db: AsyncSession = Depends(get_db),
):
    """Give a user a different role (needs roles:assign). Nobody changes their own role. Takes effect at the
    user's next token refresh."""
    try:
        if user_id == current_user.id:
            raise APIException(status_code=400, message="You can't change your own role")
        user = await UserService(db).change_role(user_id, payload.role_id)
        logger.info(f"{current_user.email} changed the role of {user.email} to {user.role_name}")
        return Response.success(data=UserResponse.model_validate(user), message="Role updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Changing the role of {user_id} failed")
        raise APIException(status_code=500, message="Could not change the role")


@router.put("/{user_id}/email/", summary="Change a user's email", responses=errors(401, 403, 404, 409, 422, 500))
async def change_email(
    user_id: UUID,
    payload: EmailChange,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_permission(Scope.USERS_CHANGE_EMAIL)),
    db: AsyncSession = Depends(get_db),
):
    """Change a user's email (needs users:change_email). The account goes back to inactive until the new address
    is verified."""
    try:
        user = await UserService(db).change_email(user_id, payload.new_email, background_tasks)
        logger.info(f"{current_user.email} changed an email address to {user.email}")
        return Response.success(data=UserResponse.model_validate(user), message="Email updated; verification needed")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Changing the email of {user_id} failed")
        raise APIException(status_code=500, message="Could not change the email")


@router.post("/{user_id}/reset-password/", summary="Send a password-reset link", responses=errors(401, 403, 404, 500))
async def reset_user_password(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_RESET_PASSWORD)),
    db: AsyncSession = Depends(get_db),
):
    """Send a user a password-reset link (needs users:reset_password)."""
    try:
        await UserService(db).reset_password(user_id)
        logger.info(f"{current_user.email} sent a password reset to user {user_id}")
        return Response.success(message="Password reset link sent")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Sending a password reset to {user_id} failed")
        raise APIException(status_code=500, message="Could not send the password reset")


@router.post("/{user_id}/deactivate/", summary="Deactivate an account", responses=errors(400, 401, 403, 404, 500))
async def deactivate_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_DEACTIVATE)),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate an account (needs users:deactivate). You can't deactivate yourself. A suspended account can't be
    deactivated."""
    try:
        if user_id == current_user.id:
            raise APIException(status_code=400, message="You can't deactivate your own account")
        await UserService(db).deactivate(user_id)
        logger.info(f"{current_user.email} deactivated user {user_id}")
        return Response.success(message="User deactivated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deactivating {user_id} failed")
        raise APIException(status_code=500, message="Could not deactivate the user")


@router.post("/{user_id}/activate/", summary="Activate an account", responses=errors(401, 403, 404, 500))
async def activate_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_ACTIVATE)),
    db: AsyncSession = Depends(get_db),
):
    """Activate an account (needs users:activate). A suspended account is lifted with /unsuspend/ instead."""
    try:
        await UserService(db).activate(user_id)
        logger.info(f"{current_user.email} activated user {user_id}")
        return Response.success(message="User activated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Activating {user_id} failed")
        raise APIException(status_code=500, message="Could not activate the user")


@router.post("/{user_id}/suspend/", summary="Suspend an account", responses=errors(400, 401, 403, 404, 500))
async def suspend_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_SUSPEND)),
    db: AsyncSession = Depends(get_db),
):
    """Suspend an account (needs users:suspend). You can't suspend yourself. The user is locked out at once, even
    with a token that hasn't expired."""
    try:
        if user_id == current_user.id:
            raise APIException(status_code=400, message="You can't suspend your own account")
        await UserService(db).suspend(user_id)
        logger.info(f"{current_user.email} suspended user {user_id}")
        return Response.success(message="User suspended")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Suspending {user_id} failed")
        raise APIException(status_code=500, message="Could not suspend the user")


@router.post("/{user_id}/unsuspend/", summary="Lift a suspension", responses=errors(400, 401, 403, 404, 500))
async def unsuspend_user(
    user_id: UUID,
    current_user: User = Depends(require_permission(Scope.USERS_UNSUSPEND)),
    db: AsyncSession = Depends(get_db),
):
    """Lift a suspension (needs users:unsuspend). The account becomes active again."""
    try:
        await UserService(db).unsuspend(user_id)
        logger.info(f"{current_user.email} unsuspended user {user_id}")
        return Response.success(message="User unsuspended")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Unsuspending {user_id} failed")
        raise APIException(status_code=500, message="Could not unsuspend the user")
