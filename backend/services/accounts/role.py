from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.accounts.role import Role
from models.accounts.user import User
from core.exceptions import APIException
from schemas.accounts.role import RoleCreate, RoleUpdate
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class RoleService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, role_data: RoleCreate) -> Role:
        """Create a new role. Raises 409 if a role with this name already exists."""
        name = role_data.name.lower()
        if await self.get_by_name(name):
            raise APIException(status_code=409, message=f"Role '{name}' already exists")

        new_role = Role(
            id=uuid7(),
            name=name,
            description=role_data.description.lower() if role_data.description else None,
        )
        self.db.add(new_role)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests for the same name raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message=f"Role '{name}' already exists")
        await self.db.refresh(new_role)

        return new_role

    async def get_by_id(self, role_id: UUID) -> Optional[Role]:
        """Get a role by its ID, or None."""
        result = await self.db.execute(select(Role).where(Role.id == role_id))
        return result.scalar_one_or_none()

    async def get_by_name(self, role_name: str) -> Optional[Role]:
        """Get a role by its name (case-insensitive), or None."""
        result = await self.db.execute(select(Role).where(func.lower(Role.name) == role_name.lower()))
        return result.scalar_one_or_none()

    async def list_roles(self, search: Optional[str] = None) -> List[Role]:
        """List all roles by name, optionally filtered by a search term on name or description."""
        query = select(Role).order_by(Role.name)
        if search:
            term = f"%{search.lower()}%"
            query = query.where(func.lower(Role.name).like(term) | func.lower(Role.description).like(term))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_role(self, role_id: UUID, role_data: RoleUpdate) -> Role:
        """Update the fields that were sent (PATCH semantics).
        Raises 404 if the role doesn't exist, 400 if nothing was sent, 409 if the new name is taken."""
        role = await self.get_by_id(role_id)
        if not role:
            raise APIException(status_code=404, message="Role not found")

        changes = role_data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")

        if "name" in changes:
            if not changes["name"]:
                raise APIException(status_code=400, message="name cannot be empty")
            name = changes["name"].lower()
            taken = await self.get_by_name(name)
            if taken and taken.id != role.id:
                raise APIException(status_code=409, message=f"Role '{name}' already exists")
            role.name = name
        if "description" in changes:
            role.description = changes["description"].lower() if changes["description"] else None

        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise APIException(status_code=409, message="A role with this name already exists")
        await self.db.refresh(role)

        return role

    async def delete_role(self, role_id: UUID) -> None:
        """Delete a role. Raises 404 if it doesn't exist, 409 if users still have it
        (deleting it would break them; reassign them first)."""
        role = await self.get_by_id(role_id)
        if not role:
            raise APIException(status_code=404, message="Role not found")

        in_use = (await self.db.execute(select(func.count()).select_from(User).where(User.role_id == role_id))).scalar()
        if in_use:
            raise APIException(status_code=409, message=f"Role is still assigned to {in_use} user(s)")

        await self.db.delete(role)
        await self.db.commit()
