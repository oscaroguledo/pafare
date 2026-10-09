from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.bookings.room_type import RoomType
from models.bookings.room import Room
from core.exceptions import APIException
from schemas.bookings.room_type import RoomTypeCreate, RoomTypeUpdate, RoomTypeList
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class RoomTypeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: RoomTypeCreate) -> RoomType:
        """Create a room type. Raises 409 if a room type with this name already exists (case-insensitive)."""
        if (await self.db.execute(select(RoomType.id).where(func.lower(RoomType.name) == data.name.lower()))).first():
            raise APIException(status_code=409, message=f"Room type '{data.name}' already exists")

        room_type = RoomType(id=uuid7(), **{**data.model_dump(), "bed": data.bed.value})
        self.db.add(room_type)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests for the same name raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message=f"Room type '{data.name}' already exists")
        await self.db.refresh(room_type)

        return room_type

    async def get(self, room_type_id: UUID) -> RoomType:
        """Get a room type by id. Raises 404 if it doesn't exist."""
        room_type = (await self.db.execute(select(RoomType).where(RoomType.id == room_type_id))).scalar_one_or_none()
        if not room_type:
            raise APIException(status_code=404, message="Room type not found")

        return room_type

    async def list(self, filter: RoomTypeList, page: int = 1, limit: int = 10) -> dict:
        """Get a paginated, filtered list of room types, cheapest first.
        Raises 400 if page is below 1, limit is outside 1-100, or min_price is above max_price."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")
        if filter.min_price is not None and filter.max_price is not None and filter.min_price > filter.max_price:
            raise APIException(status_code=400, message="min_price cannot be above max_price")

        conditions = []
        if filter.search:
            conditions.append(RoomType.name.ilike(f"%{filter.search}%"))
        if filter.bed:
            conditions.append(RoomType.bed == filter.bed.value)
        if filter.min_price is not None:
            conditions.append(RoomType.price_per_night >= filter.min_price)
        if filter.max_price is not None:
            conditions.append(RoomType.price_per_night <= filter.max_price)
        for amenity in filter.amenities or []:
            conditions.append(getattr(RoomType, amenity).is_(True))  # names were checked against AMENITIES

        total = (await self.db.execute(select(func.count()).select_from(RoomType).where(*conditions))).scalar() or 0
        room_types = (await self.db.execute(
            select(RoomType)
            .where(*conditions)
            .order_by(RoomType.price_per_night, RoomType.name, RoomType.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )).scalars().all()

        return {
            "room_types": room_types,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def update(self, room_type_id: UUID, data: RoomTypeUpdate) -> RoomType:
        """Update the fields that were sent (PATCH semantics).
        Raises 404 if the room type doesn't exist, 400 if nothing was sent or a field is set to null,
        409 if the new name belongs to another room type."""
        room_type = await self.get(room_type_id)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field, value in changes.items():
            if value is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        if "name" in changes:
            taken = (await self.db.execute(
                select(RoomType.id).where(func.lower(RoomType.name) == changes["name"].lower(), RoomType.id != room_type_id)
            )).first()
            if taken:
                raise APIException(status_code=409, message=f"Room type '{changes['name']}' already exists")
        if "bed" in changes:
            changes["bed"] = changes["bed"].value

        for field, value in changes.items():
            setattr(room_type, field, value)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise APIException(status_code=409, message="A room type with this name already exists")
        await self.db.refresh(room_type)

        return room_type

    async def delete(self, room_type_id: UUID) -> None:
        """Delete a room type. Raises 404 if it doesn't exist, 409 if rooms still use it."""
        room_type = await self.get(room_type_id)

        rooms = (await self.db.execute(select(func.count()).select_from(Room).where(Room.room_type_id == room_type_id))).scalar()
        if rooms:
            raise APIException(status_code=409, message=f"Room type is still used by {rooms} room(s)")

        await self.db.delete(room_type)
        await self.db.commit()
