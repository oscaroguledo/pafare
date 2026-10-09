from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.docs import errors
from core.db import get_db
from core.dependencies import require_permission
from core.exceptions import APIException
from core.logging import get_structured_logger
from core.scopes import Scope
from core.utils.response import Response
from models.accounts.user import User
from schemas.accounts.permission import PermissionCreate, PermissionUpdate, PermissionResponse
from services.accounts.permission import PermissionService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/permissions", tags=["Permissions"])


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a permission", responses=errors(401, 403, 409, 422, 500))
async def create_permission(
    payload: PermissionCreate,
    current_user: User = Depends(require_permission(Scope.PERMISSIONS_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a permission named resource:action (needs permissions:create). A permission only matters once a
    route checks it, so new ones normally come from core/scopes.py and a migration."""
    try:
        permission = await PermissionService(db).create(payload)
        logger.info(f"{current_user.email} created permission {permission.name}")
        return Response.success(
            data=PermissionResponse.model_validate(permission), message="Permission created", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a permission failed")
        raise APIException(status_code=500, message="Could not create the permission")


@router.get("/", summary="List permissions", responses=errors(401, 403, 500))
async def list_permissions(
    search: Optional[str] = Query(None, description="Matches name or description"),
    resource: Optional[str] = Query(None, description="The part before the colon, e.g. 'bookings'"),
    current_user: User = Depends(require_permission(Scope.PERMISSIONS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """List permissions by name (needs permissions:view)."""
    try:
        permissions = await PermissionService(db).list_permissions(search, resource)
        return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing permissions failed")
        raise APIException(status_code=500, message="Could not load the permissions")


@router.get("/{permission_id}/", summary="Get a permission", responses=errors(401, 403, 404, 500))
async def get_permission(
    permission_id: UUID,
    current_user: User = Depends(require_permission(Scope.PERMISSIONS_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    try:
        permission = await PermissionService(db).get_by_id(permission_id)
        if not permission:
            raise APIException(status_code=404, message="Permission not found")
        return Response.success(data=PermissionResponse.model_validate(permission))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching permission {permission_id} failed")
        raise APIException(status_code=500, message="Could not load the permission")


@router.patch("/{permission_id}/", summary="Update a permission's description", responses=errors(401, 403, 404, 422, 500))
async def update_permission(
    permission_id: UUID,
    payload: PermissionUpdate,
    current_user: User = Depends(require_permission(Scope.PERMISSIONS_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Only the description can change; the name is the scope code that tokens and checks refer to."""
    try:
        permission = await PermissionService(db).update_permission(permission_id, payload)
        return Response.success(data=PermissionResponse.model_validate(permission), message="Permission updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating permission {permission_id} failed")
        raise APIException(status_code=500, message="Could not update the permission")


@router.delete("/{permission_id}/", summary="Delete a permission", responses=errors(401, 403, 404, 500))
async def delete_permission(
    permission_id: UUID,
    current_user: User = Depends(require_permission(Scope.PERMISSIONS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a permission (needs permissions:delete). Every role loses it."""
    try:
        await PermissionService(db).delete_permission(permission_id)
        logger.info(f"{current_user.email} deleted permission {permission_id}")
        return Response.success(message="Permission deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting permission {permission_id} failed")
        raise APIException(status_code=500, message="Could not delete the permission")
