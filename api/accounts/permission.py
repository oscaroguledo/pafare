from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.dependencies import require_permission
from core.exceptions import APIException
from core.utils.response import Response
from models.accounts.user import User
from schemas.accounts.permission import PermissionCreate, PermissionUpdate, PermissionResponse
from services.accounts.permission import PermissionService

router = APIRouter(prefix="/permissions", tags=["Permissions"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_permission(
    payload: PermissionCreate,
    current_user: User = Depends(require_permission("permissions:create")),
    db: AsyncSession = Depends(get_db),
):
    permission = await PermissionService(db).create(payload)
    return Response.success(
        data=PermissionResponse.model_validate(permission), message="Permission created", status_code=status.HTTP_201_CREATED
    )


@router.get("/")
async def list_permissions(
    search: Optional[str] = Query(None, description="Matches name or description"),
    resource: Optional[str] = Query(None, description="The part before the colon, e.g. 'rooms'"),
    current_user: User = Depends(require_permission("permissions:view")),
    db: AsyncSession = Depends(get_db),
):
    permissions = await PermissionService(db).list_permissions(search, resource)
    return Response.success(data=[PermissionResponse.model_validate(p) for p in permissions])


@router.get("/{permission_id}/")
async def get_permission(
    permission_id: UUID,
    current_user: User = Depends(require_permission("permissions:view")),
    db: AsyncSession = Depends(get_db),
):
    permission = await PermissionService(db).get_by_id(permission_id)
    if not permission:
        raise APIException(status_code=404, message="Permission not found")
    return Response.success(data=PermissionResponse.model_validate(permission))


@router.patch("/{permission_id}/")
async def update_permission(
    permission_id: UUID,
    payload: PermissionUpdate,
    current_user: User = Depends(require_permission("permissions:edit")),
    db: AsyncSession = Depends(get_db),
):
    permission = await PermissionService(db).update_permission(permission_id, payload)
    return Response.success(data=PermissionResponse.model_validate(permission), message="Permission updated")


@router.delete("/{permission_id}/")
async def delete_permission(
    permission_id: UUID,
    current_user: User = Depends(require_permission("permissions:delete")),
    db: AsyncSession = Depends(get_db),
):
    await PermissionService(db).delete_permission(permission_id)
    return Response.success(message="Permission deleted")
