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
from schemas.accounts.permission import PermissionResponse, RolePermissionSet
from schemas.accounts.role import RoleCreate, RoleUpdate, RoleResponse
from services.accounts.permission import PermissionService
from services.accounts.role import RoleService

logger = get_structured_logger(__name__)

router = APIRouter(prefix="/roles", tags=["Roles"])


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a role", responses=errors(401, 403, 409, 422, 500))
async def create_role(
    payload: RoleCreate,
    current_user: User = Depends(require_permission(Scope.ROLES_CREATE)),
    db: AsyncSession = Depends(get_db),
):
    """Create a role (needs roles:create). It starts with no permissions; grant them with the /permissions/ routes."""
    try:
        role = await RoleService(db).create(payload)
        logger.info(f"{current_user.email} created role {role.name}")
        return Response.success(
            data=RoleResponse.model_validate(role), message="Role created", status_code=status.HTTP_201_CREATED
        )
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Creating a role failed")
        raise APIException(status_code=500, message="Could not create the role")


@router.get("/", summary="List roles", responses=errors(401, 403, 500))
async def list_roles(
    search: Optional[str] = Query(None, description="Matches name or description"),
    current_user: User = Depends(require_permission(Scope.ROLES_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    """List roles by name (needs roles:view)."""
    try:
        roles = await RoleService(db).list_roles(search)
        return Response.success(data=[RoleResponse.model_validate(r) for r in roles])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception("Listing roles failed")
        raise APIException(status_code=500, message="Could not load the roles")


@router.get("/{role_id}/", summary="Get a role", responses=errors(401, 403, 404, 500))
async def get_role(
    role_id: UUID,
    current_user: User = Depends(require_permission(Scope.ROLES_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    try:
        role = await RoleService(db).get_by_id(role_id)
        if not role:
            raise APIException(status_code=404, message="Role not found")
        return Response.success(data=RoleResponse.model_validate(role))
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Fetching role {role_id} failed")
        raise APIException(status_code=500, message="Could not load the role")


@router.patch("/{role_id}/", summary="Update a role", responses=errors(400, 401, 403, 404, 409, 422, 500))
async def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    current_user: User = Depends(require_permission(Scope.ROLES_EDIT)),
    db: AsyncSession = Depends(get_db),
):
    """Rename a role or change its description (needs roles:edit). Its permissions are changed with roles:grant."""
    try:
        role = await RoleService(db).update_role(role_id, payload)
        logger.info(f"{current_user.email} updated role {role.name}")
        return Response.success(data=RoleResponse.model_validate(role), message="Role updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Updating role {role_id} failed")
        raise APIException(status_code=500, message="Could not update the role")


@router.delete("/{role_id}/", summary="Delete a role", responses=errors(401, 403, 404, 409, 500))
async def delete_role(
    role_id: UUID,
    current_user: User = Depends(require_permission(Scope.ROLES_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a role (needs roles:delete). Refused while any user still has it."""
    try:
        await RoleService(db).delete_role(role_id)
        logger.info(f"{current_user.email} deleted role {role_id}")
        return Response.success(message="Role deleted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Deleting role {role_id} failed")
        raise APIException(status_code=500, message="Could not delete the role")


# ---- the permissions a role has ----

@router.get("/{role_id}/permissions/", summary="A role's permissions", responses=errors(401, 403, 404, 500))
async def list_role_permissions(
    role_id: UUID,
    current_user: User = Depends(require_permission(Scope.ROLES_VIEW)),
    db: AsyncSession = Depends(get_db),
):
    try:
        permissions = await PermissionService(db).list_for_role(role_id)
        return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions])
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Listing permissions of role {role_id} failed")
        raise APIException(status_code=500, message="Could not load the role's permissions")


@router.put("/{role_id}/permissions/", summary="Replace a role's permissions", responses=errors(400, 401, 403, 404, 422, 500))
async def set_role_permissions(
    role_id: UUID,
    payload: RolePermissionSet,
    current_user: User = Depends(require_permission(Scope.ROLES_GRANT)),
    db: AsyncSession = Depends(get_db),
):
    """Make the role's permissions exactly this set (needs roles:grant). Users get the change at their next request."""
    try:
        permissions = await PermissionService(db).set_for_role(role_id, payload.permission_ids)
        logger.info(f"{current_user.email} set role {role_id} to {len(permissions)} permission(s)")
        return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions], message="Permissions updated")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Setting permissions of role {role_id} failed")
        raise APIException(status_code=500, message="Could not update the role's permissions")


@router.post("/{role_id}/permissions/{permission_id}/", summary="Grant a permission to a role", responses=errors(401, 403, 404, 500))
async def grant_permission(
    role_id: UUID,
    permission_id: UUID,
    current_user: User = Depends(require_permission(Scope.ROLES_GRANT)),
    db: AsyncSession = Depends(get_db),
):
    """Give a role one permission (needs roles:grant). Granting it twice is harmless."""
    try:
        permissions = await PermissionService(db).grant(role_id, permission_id)
        logger.info(f"{current_user.email} granted permission {permission_id} to role {role_id}")
        return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions], message="Permission granted")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Granting {permission_id} to role {role_id} failed")
        raise APIException(status_code=500, message="Could not grant the permission")


@router.delete("/{role_id}/permissions/{permission_id}/", summary="Revoke a permission from a role", responses=errors(401, 403, 404, 500))
async def revoke_permission(
    role_id: UUID,
    permission_id: UUID,
    current_user: User = Depends(require_permission(Scope.ROLES_GRANT)),
    db: AsyncSession = Depends(get_db),
):
    """Take one permission away from a role (needs roles:grant)."""
    try:
        permissions = await PermissionService(db).revoke(role_id, permission_id)
        logger.info(f"{current_user.email} revoked permission {permission_id} from role {role_id}")
        return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions], message="Permission revoked")
    except (APIException, HTTPException, ValidationError):
        raise
    except Exception:
        logger.exception(f"Revoking {permission_id} from role {role_id} failed")
        raise APIException(status_code=500, message="Could not revoke the permission")
