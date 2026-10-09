from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.dependencies import require_permission
from core.exceptions import APIException
from core.utils.response import Response
from models.accounts.user import User
from schemas.accounts.permission import PermissionResponse, RolePermissionSet
from schemas.accounts.role import RoleCreate, RoleUpdate, RoleResponse
from services.accounts.permission import PermissionService
from services.accounts.role import RoleService

router = APIRouter(prefix="/roles", tags=["Roles"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    current_user: User = Depends(require_permission("roles:create")),
    db: AsyncSession = Depends(get_db),
):
    role = await RoleService(db).create(payload)
    return Response.success(data=RoleResponse.model_validate(role), message="Role created", status_code=status.HTTP_201_CREATED)


@router.get("/")
async def list_roles(
    search: Optional[str] = Query(None),
    current_user: User = Depends(require_permission("roles:view")),
    db: AsyncSession = Depends(get_db),
):
    roles = await RoleService(db).list_roles(search)
    return Response.success(data=[RoleResponse.model_validate(r) for r in roles])


@router.get("/{role_id}/")
async def get_role(
    role_id: UUID,
    current_user: User = Depends(require_permission("roles:view")),
    db: AsyncSession = Depends(get_db),
):
    role = await RoleService(db).get_by_id(role_id)
    if not role:
        raise APIException(status_code=404, message="Role not found")
    return Response.success(data=RoleResponse.model_validate(role))


@router.patch("/{role_id}/")
async def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    current_user: User = Depends(require_permission("roles:edit")),
    db: AsyncSession = Depends(get_db),
):
    role = await RoleService(db).update_role(role_id, payload)
    return Response.success(data=RoleResponse.model_validate(role), message="Role updated")


@router.delete("/{role_id}/")
async def delete_role(
    role_id: UUID,
    current_user: User = Depends(require_permission("roles:delete")),
    db: AsyncSession = Depends(get_db),
):
    await RoleService(db).delete_role(role_id)
    return Response.success(message="Role deleted")


# ---- the permissions a role has ----

@router.get("/{role_id}/permissions/")
async def list_role_permissions(
    role_id: UUID,
    current_user: User = Depends(require_permission("roles:view")),
    db: AsyncSession = Depends(get_db),
):
    permissions = await PermissionService(db).list_for_role(role_id)
    return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions])


@router.put("/{role_id}/permissions/")
async def set_role_permissions(
    role_id: UUID,
    payload: RolePermissionSet,
    current_user: User = Depends(require_permission("roles:edit")),
    db: AsyncSession = Depends(get_db),
):
    """Replace the role's permissions with exactly this set."""
    permissions = await PermissionService(db).set_for_role(role_id, payload.permission_ids)
    return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions], message="Permissions updated")


@router.post("/{role_id}/permissions/{permission_id}/")
async def grant_permission(
    role_id: UUID,
    permission_id: UUID,
    current_user: User = Depends(require_permission("roles:edit")),
    db: AsyncSession = Depends(get_db),
):
    permissions = await PermissionService(db).grant(role_id, permission_id)
    return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions], message="Permission granted")


@router.delete("/{role_id}/permissions/{permission_id}/")
async def revoke_permission(
    role_id: UUID,
    permission_id: UUID,
    current_user: User = Depends(require_permission("roles:edit")),
    db: AsyncSession = Depends(get_db),
):
    permissions = await PermissionService(db).revoke(role_id, permission_id)
    return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions], message="Permission revoked")
