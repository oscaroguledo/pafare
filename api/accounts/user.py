from fastapi import APIRouter, Depends, status, Query, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import Optional
from core.utils.response import Response
from core.exceptions import APIException
from core.db import get_db
from core.logging import get_structured_logger as get_logger
from services.accounts.user import UserService
from schemas.accounts.auth import strong_password
from schemas.accounts.user import Create as UserCreate, Update as UserUpdate, UserRoleUpdate
from core.dependencies import require_admin, require_auth
from models.accounts.user import User as AuthUser, UserRole, outranks

logger = get_logger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])


def _user_payload(user) -> dict:
    """Admin-facing user shape shared by create, update and role changes."""
    return {
        "id": str(user.id),
        "email": user.email,
        "firstname": user.firstname,
        "lastname": user.lastname,
        "phone": user.phone,
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "account_status": user.account_status,
        "verification_status": user.verification_status,
        "verified": user.verified,
        "is_active": user.is_active,
        "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
        "gender": user.gender,
        "country": user.country,
        "timezone": user.timezone,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


async def _manageable(db: AsyncSession, current_user: AuthUser, user_id: UUID) -> AuthUser:
    """The user an admin action targets, when the acting staff member ranks above them.
    Only a superadmin manages admins, and no one manages a superadmin through the API."""
    user = await db.get(AuthUser, user_id)
    if not user:
        raise APIException(status_code=status.HTTP_404_NOT_FOUND, message="User not found")
    if not outranks(current_user.role, user.role):
        raise APIException(status_code=status.HTTP_403_FORBIDDEN, message="You can only manage accounts below your own role")
    return user


def _can_give(current_user: AuthUser, role: UserRole) -> None:
    """Staff can only give roles below their own: admins are made by a superadmin, superadmins on the server."""
    if not outranks(current_user.role, role):
        raise APIException(status_code=status.HTTP_403_FORBIDDEN, message=f"You can't give the {role.value} role")


@router.post("/")
async def create(
    payload: UserCreate,
    background_tasks: BackgroundTasks = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new user (admin only - public signup is POST /v1/auth/register/)."""
    try:
        try:
            strong_password(payload.password)
        except ValueError as e:
            raise APIException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, message=str(e))
        _can_give(current_user, payload.role)
        service = UserService(db)
        user = await service.create(payload, background_tasks)
        return Response.success(data=_user_payload(user), message="User created successfully", status_code=status.HTTP_201_CREATED)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to create user: {str(e)}"
        )


@router.get("/{user_id}/")
async def get(
    user_id: UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get a user by ID. Admin can get any user, users can only get themselves."""
    try:
        # Since require_admin is used, current_user is already admin/manager
        # Admins/managers can access any user
        service = UserService(db)
        user = await service.get(user_id)
        if not user:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND, message="User not found")
        return Response.success(data=user.to_dict(), message="User retrieved successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch user: {str(e)}"
        )


@router.get("/")
async def list(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    role: Optional[str] = Query(None, description="Filter by user role"),
    q: Optional[str] = Query(None, description="Search query for user name or email"),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """List users with optional filtering and pagination (admin only)."""
    try:
        service = UserService(db)
        result = await service.list(
            page=page, limit=limit, role=role, query=search or q, status=status
        )
        if isinstance(result, dict) and "users" in result and "pagination" in result:
            return Response.success(data=result.get("users", []), pagination=result.get("pagination"))
        return Response.success(data=result)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to fetch users: {str(e)}"
        )


@router.patch("/{user_id}/")
async def patch(
    user_id: UUID,
    payload: UserUpdate,
    current_user: AuthUser = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """Partially update a user. Admin can update any user, users can only update themselves."""
    try:
        is_admin = current_user.is_staff
        # Non-admins may only touch their own record. UserUpdate itself only exposes plain profile fields (role/account_status/is_active/etc. aren't declared on it, so Pydantic already strips them - there's nothing sensitive left to gate).
        if not is_admin and current_user.id != user_id:
            raise APIException(
                status_code=status.HTTP_403_FORBIDDEN,
                message="You can only update your own user data"
            )
        if current_user.id != user_id:
            await _manageable(db, current_user, user_id)  # e.g. an admin can't change a superadmin's email
        service = UserService(db)
        updated_user = await service.update(user_id, payload)
        if not updated_user:
            raise APIException(
                status_code=status.HTTP_404_NOT_FOUND, message="User not found")
        return Response.success(data=_user_payload(updated_user), message="User updated successfully")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        raise APIException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"Failed to update user: {str(e)}"
        )


@router.post("/{user_id}/reset-password/")
async def reset_password(
    user_id: UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Send password reset email to user (admin only)."""
    try:
        await _manageable(db, current_user, user_id)
        service = UserService(db)
        result = await service.reset_password(user_id)
        return Response.success(data=result, message="Password reset email sent")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to reset password: {str(e)}")


@router.post("/{user_id}/deactivate/")
async def deactivate(
    user_id: UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Deactivate user account (admin only); admins can't lock themselves out."""
    try:
        if current_user.id == user_id:
            raise APIException(status_code=status.HTTP_400_BAD_REQUEST, message="You can't deactivate your own account")
        await _manageable(db, current_user, user_id)
        service = UserService(db)
        result = await service.deactivate(user_id)
        return Response.success(data=result, message="User deactivated")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to deactivate user: {str(e)}")


@router.post("/{user_id}/activate/")
async def activate(
    user_id: UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Activate user account (admin only)."""
    try:
        await _manageable(db, current_user, user_id)
        service = UserService(db)
        result = await service.activate(user_id)
        return Response.success(data=result, message="User activated")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to activate user: {str(e)}")


@router.put("/{user_id}/verify/")
async def verify(
    user_id: UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Verify user account (admin only)."""
    try:
        await _manageable(db, current_user, user_id)
        service = UserService(db)
        result = await service.verify_user_account(user_id)
        return Response.success(data=result, message="User verified")
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        raise APIException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"Failed to verify user: {str(e)}")


@router.put("/{user_id}/role/")
async def update_role(
    user_id: UUID,
    payload: UserRoleUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Change a user's role. Staff can only change accounts below their own role, to roles below their own:
    a superadmin makes and removes admins, and nobody changes their own role."""
    if user_id == current_user.id:
        raise APIException(status_code=status.HTTP_400_BAD_REQUEST, message="You can't change your own role")
    user = await _manageable(db, current_user, user_id)
    _can_give(current_user, payload.role)
    user.role = payload.role
    await db.commit()
    await db.refresh(user)
    return Response.success(data=_user_payload(user), message="Role updated")
