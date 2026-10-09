from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_, or_, func
from typing import Optional, Dict, Any
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.accounts.user import Address
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class AddressService:
    """Service layer for managing user addresses."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # --- CRUD operations ---

    async def create(
        self,
        user_id: UUID,
        street: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        country: Optional[str] = None,
        post_code: Optional[str] = None,
        **kwargs
    ) -> Address:
        """Create a new address for a user."""
        address = Address(
            id=uuid7(),
            user_id=user_id,
            street=street or "",
            city=city or "",
            state=state or "",
            country=country or "",
            post_code=post_code or "",
        )
        self.db.add(address)
        await self.db.commit()
        await self.db.refresh(address)
        return address

    async def get(self, address_id: UUID) -> Optional[Address]:
        """Retrieve an address by ID."""
        query = select(Address).where(Address.id == address_id)
        result = await self.db.execute(query)
        return result.scalars().first()

    async def list(self, user_id: UUID, page: int = 1, limit: int = 10, search: Optional[str] = None) -> Dict[str, Any]:
        """Fetch addresses for a given user with pagination and search. First = default."""
        offset = (page - 1) * limit

        # Build base query with optional search
        base_conditions = [Address.user_id == user_id]
        if search:
            search_pattern = f"%{search}%"
            base_conditions.append(
                or_(
                    Address.street.ilike(search_pattern),
                    Address.city.ilike(search_pattern),
                    Address.state.ilike(search_pattern),
                    Address.country.ilike(search_pattern),
                    Address.post_code.ilike(search_pattern)
                )
            )

        count_query = select(func.count()).select_from(Address).where(and_(*base_conditions))
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()

        query = (
            select(Address)
            .where(and_(*base_conditions))
            .order_by(Address.created_at.asc())  # oldest first = default
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(query)
        addresses = result.scalars().all()
        return {
            "data": addresses,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit if limit else 1
            }
        }

    async def update(self, address_id: UUID, user_id: UUID, **kwargs) -> Optional[Address]:
        """Update address fields dynamically."""
        query = update(Address)
        query = query.where(and_(Address.id == address_id, Address.user_id == user_id))
        query = query.values(**kwargs)
        query = query.execution_options(synchronize_session="fetch")

        await self.db.execute(query)
        await self.db.commit()

        return await self.get(address_id)

    async def delete(self, address_id: UUID, user_id: UUID = None) -> bool:
        """Delete an address by ID."""
        if user_id:
            result = await self.db.execute(delete(Address).where(and_(Address.id == address_id, Address.user_id == user_id)))
        else:
            result = await self.db.execute(delete(Address).where(Address.id == address_id))

        await self.db.commit()
        return result.rowcount > 0

    # --- Custom logic ---

    async def default(self, user_id: UUID) -> Optional[Address]:
        """Get a user's first (default) address."""
        query = select(Address).where(
            Address.user_id == user_id
        ).order_by(Address.created_at.asc())
        result = await self.db.execute(query)
        return result.scalars().first()
