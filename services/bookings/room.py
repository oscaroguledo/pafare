from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.bookings.hotel import Hotel
from models.bookings.room_type import RoomType
from models.bookings.room import Room
from models.bookings.booking import Booking
from core.exceptions import APIException
from schemas.bookings.room import RoomCreate, RoomUpdate, RoomList
from services.bookings.booking import ACTIVE_STATUSES
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class RoomService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: RoomCreate) -> Room:
        """Create a room in a hotel. Raises 404 if the hotel or room type doesn't exist,
        409 if the hotel already has a room with this number."""
        hotel_name = (await self.db.execute(select(Hotel.name).where(Hotel.id == data.hotel_id))).scalar_one_or_none()
        if hotel_name is None:
            raise APIException(status_code=404, message="Hotel not found")
        room_type_name = (await self.db.execute(select(RoomType.name).where(RoomType.id == data.room_type_id))).scalar_one_or_none()
        if room_type_name is None:
            raise APIException(status_code=404, message="Room type not found")
        if (await self.db.execute(select(Room.id).where(Room.hotel_id == data.hotel_id, Room.number == data.number))).first():
            raise APIException(status_code=409, message=f"The hotel already has a room '{data.number}'")

        room = Room(id=uuid7(), **data.model_dump())
        self.db.add(room)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests for the same number raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message=f"The hotel already has a room '{data.number}'")
        await self.db.refresh(room)

        room.hotel_name, room.room_type_name = hotel_name, room_type_name
        return room

    async def get(self, room_id: UUID) -> Room:
        """Get a room with its hotel and room type names. Raises 404 if it doesn't exist."""
        row = (await self.db.execute(
            select(Room, Hotel.name, RoomType.name)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(Room.id == room_id)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Room not found")
        room, hotel_name, room_type_name = row
        room.hotel_name, room.room_type_name = hotel_name, room_type_name

        return room

    async def list(self, filter: RoomList, page: int = 1, limit: int = 10) -> dict:
        """Get a paginated, filtered list of rooms by hotel and number. With available_from/available_to, only rooms
        with no active booking overlapping those nights.
        Raises 400 if page is below 1 or limit is outside 1-100."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")

        conditions = []
        if filter.hotel_id:
            conditions.append(Room.hotel_id == filter.hotel_id)
        if filter.room_type_id:
            conditions.append(Room.room_type_id == filter.room_type_id)
        if filter.search:
            conditions.append(Room.number.ilike(f"%{filter.search}%"))
        if filter.available_from and filter.available_to:
            taken = select(Booking.room_id).where(
                Booking.status.in_(ACTIVE_STATUSES),
                Booking.check_in < filter.available_to,
                Booking.check_out > filter.available_from,
            )
            conditions.append(Room.id.notin_(taken))

        total = (await self.db.execute(select(func.count()).select_from(Room).where(*conditions))).scalar() or 0
        rows = (await self.db.execute(
            select(Room, Hotel.name, RoomType.name)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(*conditions)
            .order_by(Hotel.name, Room.number, Room.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

        rooms = []
        for room, hotel_name, room_type_name in rows:
            room.hotel_name, room.room_type_name = hotel_name, room_type_name
            rooms.append(room)

        return {
            "rooms": rooms,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def update(self, room_id: UUID, data: RoomUpdate) -> Room:
        """Update the fields that were sent (PATCH semantics).
        Raises 404 if the room or the new room type doesn't exist, 400 if nothing was sent or a field is null,
        409 if the number is already used in this hotel."""
        room = await self.get(room_id)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field, value in changes.items():
            if value is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        hotel_name, room_type_name = room.hotel_name, room.room_type_name
        if "room_type_id" in changes:
            room_type_name = (await self.db.execute(select(RoomType.name).where(RoomType.id == changes["room_type_id"]))).scalar_one_or_none()
            if room_type_name is None:
                raise APIException(status_code=404, message="Room type not found")
        if "number" in changes:
            taken = (await self.db.execute(
                select(Room.id).where(Room.hotel_id == room.hotel_id, Room.number == changes["number"], Room.id != room_id)
            )).first()
            if taken:
                raise APIException(status_code=409, message=f"The hotel already has a room '{changes['number']}'")

        for field, value in changes.items():
            setattr(room, field, value)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise APIException(status_code=409, message="The hotel already has a room with this number")
        await self.db.refresh(room)

        room.hotel_name, room.room_type_name = hotel_name, room_type_name
        return room

    async def delete(self, room_id: UUID) -> None:
        """Delete a room. Raises 404 if it doesn't exist, 409 if it has bookings (past ones included)."""
        room = await self.get(room_id)

        bookings = (await self.db.execute(select(func.count()).select_from(Booking).where(Booking.room_id == room_id))).scalar()
        if bookings:
            raise APIException(status_code=409, message=f"The room has {bookings} booking(s) and can't be deleted")

        await self.db.delete(room)
        await self.db.commit()
