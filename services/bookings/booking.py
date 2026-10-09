import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from models.bookings.booking import Booking, BookingStatus
from models.bookings.hotel import Hotel
from models.bookings.room import Room
from models.bookings.room_type import RoomType
from schemas.bookings.booking import BookingCreate, BookingUpdate, BookingList

logger = get_structured_logger(__name__)

# A booking in one of these states holds its room for its dates. Cancelled, no-show and finished stays don't.
ACTIVE_STATUSES = (BookingStatus.PENDING.value, BookingStatus.CONFIRMED.value, BookingStatus.CHECKED_IN.value)

# Which status can follow which. Anything not listed is refused.
TRANSITIONS = {
    BookingStatus.PENDING.value: {BookingStatus.CONFIRMED.value, BookingStatus.CANCELLED.value},
    BookingStatus.CONFIRMED.value: {BookingStatus.CHECKED_IN.value, BookingStatus.CANCELLED.value, BookingStatus.NO_SHOW.value},
    BookingStatus.CHECKED_IN.value: {BookingStatus.CHECKED_OUT.value},
}


class BookingService:
    """Bookings. Methods that take `user_id` only see that guest's bookings (a booking belonging to someone
    else is reported as not found); leave it out for staff."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: UUID, data: BookingCreate) -> Booking:
        """Book a room for a guest. The server sets the hotel, currency, prices, status and booking code.
        Raises 400 if check-in is in the past, 404 if the room doesn't exist,
        409 if the room is already booked for any of the nights."""
        if data.check_in < datetime.now(timezone.utc).date():
            raise APIException(status_code=400, message="check_in cannot be in the past")

        # Lock the room row so two guests booking the same room at the same time are handled one after the other
        row = (await self.db.execute(
            select(Room, Hotel, RoomType)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(Room.id == data.room_id)
            .with_for_update(of=Room)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Room not found")
        room, hotel, room_type = row

        if (await self.db.execute(
            select(Booking.id).where(
                Booking.room_id == room.id,
                Booking.status.in_(ACTIVE_STATUSES),
                Booking.check_in < data.check_out,
                Booking.check_out > data.check_in,
            )
        )).first():
            raise APIException(status_code=409, message="The room is not available for these dates")

        base_price = room_type.price_per_night * (data.check_out - data.check_in).days
        booking = Booking(
            id=uuid7(),
            hotel_id=hotel.id,
            user_id=user_id,
            room_id=room.id,
            check_in=data.check_in,
            check_out=data.check_out,
            check_in_time=data.check_in_time,
            check_out_time=data.check_out_time,
            guests=data.guests,
            status=BookingStatus.PENDING.value,
            base_price=base_price,
            discount=0,
            total_price=base_price,
            booking_code="PF-" + secrets.token_hex(4).upper(),
            currency=hotel.currency,
        )
        hotel_name, room_number, room_type_name = hotel.name, room.number, room_type.name
        self.db.add(booking)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise APIException(status_code=409, message="The booking could not be completed; please try again")
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        return booking

    async def get(self, booking_id: UUID, user_id: Optional[UUID] = None) -> Booking:
        """Get a booking with its hotel, room number and room type name. Raises 404 if it doesn't exist
        (or isn't this guest's, when user_id is given)."""
        conditions = [Booking.id == booking_id]
        if user_id is not None:
            conditions.append(Booking.user_id == user_id)
        row = (await self.db.execute(
            select(Booking, Hotel.name, Room.number, RoomType.name)
            .join(Hotel, Booking.hotel_id == Hotel.id)
            .join(Room, Booking.room_id == Room.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(*conditions)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Booking not found")
        booking, hotel_name, room_number, room_type_name = row
        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name

        return booking

    async def list(self, filter: BookingList, page: int = 1, limit: int = 10, user_id: Optional[UUID] = None) -> dict:
        """Get a paginated, filtered list of bookings, newest first. When user_id is given, only that guest's.
        Raises 400 if page is below 1, limit is outside 1-100, or check_in_from is after check_in_to."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")
        if filter.check_in_from and filter.check_in_to and filter.check_in_from > filter.check_in_to:
            raise APIException(status_code=400, message="check_in_from cannot be after check_in_to")

        conditions = []
        owner = user_id if user_id is not None else filter.user_id
        if owner is not None:
            conditions.append(Booking.user_id == owner)
        if filter.hotel_id:
            conditions.append(Booking.hotel_id == filter.hotel_id)
        if filter.room_id:
            conditions.append(Booking.room_id == filter.room_id)
        if filter.status:
            conditions.append(Booking.status == filter.status.value)
        if filter.code:
            conditions.append(func.upper(Booking.booking_code) == filter.code.upper())
        if filter.check_in_from:
            conditions.append(Booking.check_in >= filter.check_in_from)
        if filter.check_in_to:
            conditions.append(Booking.check_in <= filter.check_in_to)

        total = (await self.db.execute(select(func.count()).select_from(Booking).where(*conditions))).scalar() or 0
        rows = (await self.db.execute(
            select(Booking, Hotel.name, Room.number, RoomType.name)
            .join(Hotel, Booking.hotel_id == Hotel.id)
            .join(Room, Booking.room_id == Room.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(*conditions)
            .order_by(Booking.created_at.desc(), Booking.id.desc())  # id breaks ties so pages never repeat or skip
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

        bookings = []
        for booking, hotel_name, room_number, room_type_name in rows:
            booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
            bookings.append(booking)

        return {
            "bookings": bookings,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def update(self, booking_id: UUID, data: BookingUpdate, user_id: Optional[UUID] = None) -> Booking:
        """Reschedule a booking or change its party size (PATCH semantics). If the dates change, the price is
        recalculated at the room type's current nightly rate and the discount is kept.
        Raises 404 if the booking doesn't exist, 400 if nothing was sent, a field is null, or the new check-in is
        in the past or not before check-out, 409 if the booking is no longer changeable or the room is taken."""
        booking = await self.get(booking_id, user_id)
        if booking.status not in (BookingStatus.PENDING.value, BookingStatus.CONFIRMED.value):
            raise APIException(status_code=409, message=f"A {booking.status} booking can't be changed")

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field in ("check_in", "check_out", "guests"):
            if field in changes and changes[field] is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        check_in = changes.get("check_in", booking.check_in)
        check_out = changes.get("check_out", booking.check_out)
        if check_out <= check_in:
            raise APIException(status_code=400, message="check_out must be after check_in")
        dates_changed = (check_in, check_out) != (booking.check_in, booking.check_out)

        if dates_changed:
            if "check_in" in changes and check_in < datetime.now(timezone.utc).date():
                raise APIException(status_code=400, message="check_in cannot be in the past")
            # Lock the room row so a competing booking for the same room waits for this change
            await self.db.execute(select(Room.id).where(Room.id == booking.room_id).with_for_update())
            room_type = (await self.db.execute(
                select(RoomType).join(Room, Room.room_type_id == RoomType.id).where(Room.id == booking.room_id)
            )).scalar_one()
            if (await self.db.execute(
                select(Booking.id).where(
                    Booking.room_id == booking.room_id,
                    Booking.id != booking.id,
                    Booking.status.in_(ACTIVE_STATUSES),
                    Booking.check_in < check_out,
                    Booking.check_out > check_in,
                )
            )).first():
                raise APIException(status_code=409, message="The room is not available for these dates")
            base_price = room_type.price_per_night * (check_out - check_in).days
            changes["base_price"] = base_price
            changes["total_price"] = base_price * (100 - booking.discount) // 100

        hotel_name, room_number, room_type_name = booking.hotel_name, booking.room_number, booking.room_type_name
        for field, value in changes.items():
            setattr(booking, field, value)
        await self.db.commit()
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        return booking

    async def set_discount(self, booking_id: UUID, discount: int) -> Booking:
        """Set a percentage discount (0-100) on a booking and recalculate its total (staff).
        Raises 404 if the booking doesn't exist, 400 if the discount is outside 0-100,
        409 if the booking is no longer open."""
        if not 0 <= discount <= 100:
            raise APIException(status_code=400, message="discount must be between 0 and 100")
        booking = await self.get(booking_id)
        if booking.status not in ACTIVE_STATUSES:
            raise APIException(status_code=409, message=f"A {booking.status} booking can't be changed")

        hotel_name, room_number, room_type_name = booking.hotel_name, booking.room_number, booking.room_type_name
        booking.discount = discount
        booking.total_price = booking.base_price * (100 - discount) // 100
        await self.db.commit()
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        return booking

    async def change_status(self, booking_id: UUID, status: BookingStatus, user_id: Optional[UUID] = None) -> Booking:
        """Move a booking to a new status: pending -> confirmed or cancelled; confirmed -> checked in, cancelled or
        no-show; checked in -> checked out. Checking in or out stamps the time if none was set.
        Raises 404 if the booking doesn't exist, 409 if the move isn't allowed."""
        booking = await self.get(booking_id, user_id)
        if status.value not in TRANSITIONS.get(booking.status, set()):
            raise APIException(status_code=409, message=f"A {booking.status} booking can't become {status.value}")

        hotel_name, room_number, room_type_name = booking.hotel_name, booking.room_number, booking.room_type_name
        booking.status = status.value
        now = datetime.now(timezone.utc)
        if status == BookingStatus.CHECKED_IN and booking.check_in_time is None:
            booking.check_in_time = now
        if status == BookingStatus.CHECKED_OUT and booking.check_out_time is None:
            booking.check_out_time = now
        await self.db.commit()
        await self.db.refresh(booking)

        booking.hotel_name, booking.room_number, booking.room_type_name = hotel_name, room_number, room_type_name
        return booking

    async def cancel(self, booking_id: UUID, user_id: Optional[UUID] = None) -> Booking:
        """Cancel a pending or confirmed booking, which frees the room.
        Raises 404 if the booking doesn't exist, 409 if it can't be cancelled any more."""
        return await self.change_status(booking_id, BookingStatus.CANCELLED, user_id)
