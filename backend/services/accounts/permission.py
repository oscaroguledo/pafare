from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Set
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.accounts.role import Permission, Role_Permission
from core.exceptions import APIException
from schemas.accounts.permission import PermissionCreate, PermissionUpdate
from services.accounts.role import RoleService
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class PermissionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.role_service = RoleService(db)

    # ---- permissions ----

    async def create(self, data: PermissionCreate) -> Permission:
        """Create a permission. Raises 409 if the name already exists."""
        if await self.get_by_name(data.name):
            raise APIException(status_code=409, message=f"Permission '{data.name}' already exists")

        permission = Permission(
            id=uuid7(),
            name=data.name,
            description=data.description,
        )
        self.db.add(permission)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests for the same name raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message=f"Permission '{data.name}' already exists")
        await self.db.refresh(permission)
        return permission

    async def get_by_id(self, permission_id: UUID) -> Optional[Permission]:
        """Get a permission by its ID, or None."""
        return (await self.db.execute(select(Permission).where(Permission.id == permission_id))).scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Permission]:
        """Get a permission by its name (case-insensitive), or None."""
        return (await self.db.execute(
            select(Permission).where(func.lower(Permission.name) == name.lower())
        )).scalar_one_or_none()

    async def list_permissions(self, search: Optional[str] = None, resource: Optional[str] = None) -> List[Permission]:
        """List permissions by name. `search` matches name or description; `resource` matches the part before
        the colon (e.g. 'rooms' returns rooms:view, rooms:edit, ...)."""
        query = select(Permission).order_by(Permission.name)
        if resource:
            query = query.where(Permission.name.like(f"{resource.lower()}:%"))
        if search:
            term = f"%{search.lower()}%"
            query = query.where(func.lower(Permission.name).like(term) | func.lower(Permission.description).like(term))
        return list((await self.db.execute(query)).scalars().all())

    async def update_permission(self, permission_id: UUID, data: PermissionUpdate) -> Permission:
        """Change a permission's description. Raises 404 if it doesn't exist."""
        permission = await self.get_by_id(permission_id)
        if not permission:
            raise APIException(status_code=404, message="Permission not found")

        permission.description = data.description
        await self.db.commit()
        await self.db.refresh(permission)
        return permission

    async def delete_permission(self, permission_id: UUID) -> None:
        """Delete a permission. Roles lose it too (their grants are removed by the database).
        Raises 404 if it doesn't exist."""
        permission = await self.get_by_id(permission_id)
        if not permission:
            raise APIException(status_code=404, message="Permission not found")

        await self.db.delete(permission)
        await self.db.commit()

    # ---- what a role can do ----

    async def list_for_role(self, role_id: UUID) -> List[Permission]:
        """The permissions granted to a role, by name. Raises 404 if the role doesn't exist."""
        if not await self.role_service.get_by_id(role_id):
            raise APIException(status_code=404, message="Role not found")
        return list((await self.db.execute(
            select(Permission)
            .join(Role_Permission, Role_Permission.permission_id == Permission.id)
            .where(Role_Permission.role_id == role_id)
            .order_by(Permission.name)
        )).scalars().all())

    async def get_scopes(self, role_id: UUID) -> Set[str]:
        """The scope names (e.g. {'rooms:view'}) granted to a role, for token claims and checks.
        An unknown role simply has none."""
        rows = await self.db.execute(
            select(Permission.name)
            .join(Role_Permission, Role_Permission.permission_id == Permission.id)
            .where(Role_Permission.role_id == role_id)
        )
        return set(rows.scalars())

    async def has_permission(self, role_id: UUID, name: str) -> bool:
        """True if the role has the named permission."""
        return (await self.db.execute(
            select(Role_Permission.id)
            .join(Permission, Role_Permission.permission_id == Permission.id)
            .where(Role_Permission.role_id == role_id, Permission.name == name.lower())
        )).first() is not None

    async def grant(self, role_id: UUID, permission_id: UUID) -> List[Permission]:
        """Give a role a permission and return the role's permissions. Granting twice is harmless.
        Raises 404 if the role or permission doesn't exist."""
        if not await self.role_service.get_by_id(role_id):
            raise APIException(status_code=404, message="Role not found")
        if not await self.get_by_id(permission_id):
            raise APIException(status_code=404, message="Permission not found")

        already = (await self.db.execute(
            select(Role_Permission.id).where(Role_Permission.role_id == role_id, Role_Permission.permission_id == permission_id)
        )).first()
        if not already:
            self.db.add(Role_Permission(id=uuid7(), role_id=role_id, permission_id=permission_id))
            try:
                await self.db.commit()
            except IntegrityError:
                await self.db.rollback()  # a concurrent grant won; the result is the same
        return await self.list_for_role(role_id)

    async def revoke(self, role_id: UUID, permission_id: UUID) -> List[Permission]:
        """Take a permission away from a role and return the role's remaining permissions.
        Raises 404 if the role doesn't exist or doesn't have that permission."""
        if not await self.role_service.get_by_id(role_id):
            raise APIException(status_code=404, message="Role not found")
        result = await self.db.execute(
            delete(Role_Permission).where(Role_Permission.role_id == role_id, Role_Permission.permission_id == permission_id)
        )
        if result.rowcount == 0:
            await self.db.rollback()
            raise APIException(status_code=404, message="The role does not have this permission")
        await self.db.commit()
        return await self.list_for_role(role_id)

    async def set_for_role(self, role_id: UUID, permission_ids: List[UUID]) -> List[Permission]:
        """Replace a role's permissions with exactly this set and return them.
        Raises 404 if the role doesn't exist, 400 if any permission id is unknown."""
        if not await self.role_service.get_by_id(role_id):
            raise APIException(status_code=404, message="Role not found")
        wanted = set(permission_ids)
        if wanted:
            found = set((await self.db.execute(select(Permission.id).where(Permission.id.in_(wanted)))).scalars())
            if found != wanted:
                raise APIException(status_code=400, message=f"Unknown permission id(s): {sorted(str(i) for i in wanted - found)}")

        current = set((await self.db.execute(
            select(Role_Permission.permission_id).where(Role_Permission.role_id == role_id)
        )).scalars())
        if current - wanted:
            await self.db.execute(
                delete(Role_Permission).where(Role_Permission.role_id == role_id, Role_Permission.permission_id.in_(current - wanted))
            )
        for permission_id in wanted - current:
            self.db.add(Role_Permission(id=uuid7(), role_id=role_id, permission_id=permission_id))
        await self.db.commit()
        return await self.list_for_role(role_id)
