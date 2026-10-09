from typing import List, Optional, Set
from uuid import UUID

from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import APIException
from core.scopes import Scope
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from models.accounts.role import Role
from models.accounts.user import User, HotelStaff
from models.bookings.booking import Booking
from models.bookings.hotel import Hotel
from models.bookings.room import Room
from models.bookings.room_type import RoomType
from schemas.bookings.hotel import HotelCreate, HotelUpdate, HotelList
from services.accounts.permission import PermissionService

logger = get_structured_logger(__name__)


class HotelService:
    """Hotels and the staff who work at them. Reading hotels is public. A staff member's permissions apply only to the
    hotels they are assigned to, unless their role holds hotels:access_all. Methods that change something take
    `hotel_ids`, the hotels the staff member may work on (None = all); a hotel outside it is refused with 403."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def ensure_access(hotel_id: UUID, hotel_ids: Optional[Set[UUID]]) -> None:
        """Raise 403 if the hotel is outside the staff member's hotels (None = no limit). Used for hotel, room-type,
        room and rate changes, where the record is public anyway, so saying 'not yours' reveals nothing."""
        if hotel_ids is not None and hotel_id not in hotel_ids:
            raise APIException(status_code=403, message="You don't manage this hotel")

    async def hotel_ids_for(self, user: User) -> Optional[Set[UUID]]:
        """The hotels this user may work on: None for every hotel (their role holds hotels:access_all), otherwise the
        set of hotels they are assigned to (possibly empty)."""
        if await PermissionService(self.db).has_permission(user.role_id, Scope.HOTELS_ACCESS_ALL):
            return None
        return set((await self.db.execute(select(HotelStaff.hotel_id).where(HotelStaff.user_id == user.id))).scalars())

    async def create(self, data: HotelCreate, creator_id: Optional[UUID] = None) -> Hotel:
        """Create a hotel. When creator_id is given, that staff member is assigned to it, so a hotel-limited
        manager can work on the hotel they just made."""
        hotel = Hotel(id=uuid7(), **{**data.model_dump(), "currency": data.currency.value})
        self.db.add(hotel)
        if creator_id is not None:
            await self.db.flush()
            self.db.add(HotelStaff(id=uuid7(), hotel_id=hotel.id, user_id=creator_id))
        await self.db.commit()
        await self.db.refresh(hotel)

        return hotel

    async def get(self, hotel_id: UUID) -> Hotel:
        """Get a hotel by id. Raises 404 if it doesn't exist."""
        hotel = (await self.db.execute(select(Hotel).where(Hotel.id == hotel_id))).scalar_one_or_none()
        if not hotel:
            raise APIException(status_code=404, message="Hotel not found")

        return hotel

    async def list(self, filter: HotelList, page: int = 1, limit: int = 10) -> dict:
        """Get a paginated, filtered list of hotels by name.
        Raises 400 if page is below 1 or limit is outside 1-100."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")

        conditions = []
        if filter.search:
            term = f"%{filter.search}%"
            conditions.append(or_(Hotel.name.ilike(term), Hotel.city.ilike(term), Hotel.country.ilike(term)))
        if filter.city:
            conditions.append(Hotel.city.ilike(f"%{filter.city}%"))
        if filter.country:
            conditions.append(Hotel.country.ilike(f"%{filter.country}%"))
        if filter.currency:
            conditions.append(Hotel.currency == filter.currency.value)

        total = (await self.db.execute(select(func.count()).select_from(Hotel).where(*conditions))).scalar() or 0
        hotels = (await self.db.execute(
            select(Hotel)
            .where(*conditions)
            .order_by(Hotel.name, Hotel.id)  # id breaks ties so pages never repeat or skip
            .offset((page - 1) * limit)
            .limit(limit)
        )).scalars().all()

        return {
            "hotels": hotels,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }

    async def update(self, hotel_id: UUID, data: HotelUpdate, hotel_ids: Optional[Set[UUID]] = None) -> Hotel:
        """Update the fields that were sent (PATCH semantics). Tax and cancellation changes apply to future bookings.
        Raises 404 if the hotel doesn't exist, 403 if it isn't one of the caller's, 400 if nothing was sent or a
        required field is set to null, 409 if the currency changes while the hotel has bookings."""
        hotel = await self.get(hotel_id)
        self.ensure_access(hotel_id, hotel_ids)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field in ("name", "currency", "tax_percent", "free_cancellation_hours", "late_cancellation_fee_percent"):
            if field in changes and changes[field] is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        if "currency" in changes:
            changes["currency"] = changes["currency"].value
            if changes["currency"] != hotel.currency:
                bookings = (await self.db.execute(
                    select(func.count()).select_from(Booking).where(Booking.hotel_id == hotel_id)
                )).scalar()
                if bookings:
                    raise APIException(status_code=409, message=f"Currency can't change: the hotel has {bookings} booking(s)")

        for field, value in changes.items():
            setattr(hotel, field, value)
        await self.db.commit()
        await self.db.refresh(hotel)

        return hotel

    async def delete(self, hotel_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> None:
        """Delete a hotel. Raises 404 if it doesn't exist, 403 if it isn't one of the caller's,
        409 if it still has rooms, room types or bookings."""
        hotel = await self.get(hotel_id)
        self.ensure_access(hotel_id, hotel_ids)

        rooms = (await self.db.execute(select(func.count()).select_from(Room).where(Room.hotel_id == hotel_id))).scalar()
        if rooms:
            raise APIException(status_code=409, message=f"Hotel still has {rooms} room(s); delete them first")
        room_types = (await self.db.execute(select(func.count()).select_from(RoomType).where(RoomType.hotel_id == hotel_id))).scalar()
        if room_types:
            raise APIException(status_code=409, message=f"Hotel still has {room_types} room type(s); delete them first")
        bookings = (await self.db.execute(select(func.count()).select_from(Booking).where(Booking.hotel_id == hotel_id))).scalar()
        if bookings:
            raise APIException(status_code=409, message=f"Hotel has {bookings} booking(s) and can't be deleted")

        await self.db.delete(hotel)
        await self.db.commit()

    async def assign_staff(self, hotel_id: UUID, user_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> dict:
        """Assign a user to a hotel; assigning someone already there is harmless.
        Raises 404 if the hotel or user doesn't exist, 403 if the hotel isn't one of the caller's."""
        await self.get(hotel_id)
        self.ensure_access(hotel_id, hotel_ids)
        if not (await self.db.execute(select(User.id).where(User.id == user_id))).first():
            raise APIException(status_code=404, message="User not found")

        if not (await self.db.execute(
            select(HotelStaff.id).where(HotelStaff.hotel_id == hotel_id, HotelStaff.user_id == user_id)
        )).first():
            self.db.add(HotelStaff(id=uuid7(), hotel_id=hotel_id, user_id=user_id))
            try:
                await self.db.commit()
            except IntegrityError:
                await self.db.rollback()  # a concurrent assignment won; the result is the same
        return next(member for member in await self.list_staff(hotel_id) if member["user_id"] == user_id)

    async def unassign_staff(self, hotel_id: UUID, user_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> None:
        """Remove a user from a hotel. Raises 404 if the hotel doesn't exist or the user isn't assigned to it,
        403 if the hotel isn't one of the caller's."""
        await self.get(hotel_id)
        self.ensure_access(hotel_id, hotel_ids)
        assignment = (await self.db.execute(
            select(HotelStaff).where(HotelStaff.hotel_id == hotel_id, HotelStaff.user_id == user_id)
        )).scalar_one_or_none()
        if not assignment:
            raise APIException(status_code=404, message="That user isn't assigned to this hotel")

        await self.db.delete(assignment)
        await self.db.commit()

    async def list_staff(self, hotel_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> List[dict]:
        """The staff assigned to a hotel. Raises 404 if the hotel doesn't exist, 403 if it isn't one of the caller's."""
        await self.get(hotel_id)
        self.ensure_access(hotel_id, hotel_ids)

        rows = (await self.db.execute(
            select(HotelStaff.user_id, User.email, User.firstname, User.lastname, Role.name, HotelStaff.created_at)
            .join(User, HotelStaff.user_id == User.id)
            .join(Role, User.role_id == Role.id)
            .where(HotelStaff.hotel_id == hotel_id)
            .order_by(User.lastname, User.firstname, User.id)
        )).all()
        return [
            {"user_id": user_id, "email": email, "firstname": firstname, "lastname": lastname, "role_name": role_name, "assigned_at": assigned_at}
            for user_id, email, firstname, lastname, role_name, assigned_at in rows
        ]
