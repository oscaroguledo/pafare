from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.bookings.hotel import Hotel
from models.bookings.room import Room
from models.bookings.booking import Booking
from core.exceptions import APIException
from schemas.bookings.hotel import HotelCreate, HotelUpdate, HotelList
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class HotelService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: HotelCreate) -> Hotel:
        """Create a hotel."""
        hotel = Hotel(id=uuid7(), **{**data.model_dump(), "currency": data.currency.value})
        self.db.add(hotel)
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

    async def update(self, hotel_id: UUID, data: HotelUpdate) -> Hotel:
        """Update the fields that were sent (PATCH semantics).
        Raises 404 if the hotel doesn't exist, 400 if nothing was sent or name/currency is set to null,
        409 if the currency changes while the hotel has bookings (their prices are in the old currency)."""
        hotel = await self.get(hotel_id)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field in ("name", "currency"):
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

    async def delete(self, hotel_id: UUID) -> None:
        """Delete a hotel. Raises 404 if it doesn't exist, 409 if it still has rooms or bookings."""
        hotel = await self.get(hotel_id)

        rooms = (await self.db.execute(select(func.count()).select_from(Room).where(Room.hotel_id == hotel_id))).scalar()
        if rooms:
            raise APIException(status_code=409, message=f"Hotel still has {rooms} room(s); delete them first")
        bookings = (await self.db.execute(select(func.count()).select_from(Booking).where(Booking.hotel_id == hotel_id))).scalar()
        if bookings:
            raise APIException(status_code=409, message=f"Hotel has {bookings} booking(s) and can't be deleted")

        await self.db.delete(hotel)
        await self.db.commit()
