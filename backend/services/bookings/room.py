from typing import Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import select, func, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import APIException
from core.logging import get_structured_logger
from core.utils.uuid_utils import uuid7
from models.bookings.booking import Booking
from models.bookings.hotel import Hotel
from models.bookings.review import Review
from models.bookings.room import Room
from models.bookings.room_type import RoomRate
from models.bookings.room_type import RoomType
from schemas.bookings.room import RoomCreate, RoomUpdate, RoomList, SearchQuery, SearchSort
from schemas.bookings.room_type import AMENITIES
from services.bookings.booking import BookingService
from services.bookings.hotel import HotelService
from services.bookings.room_type import RoomTypeService

logger = get_structured_logger(__name__)


class RoomService:
    """Rooms and the search for rooms to book. Reading is public. Methods that change a room take `hotel_ids`, the hotels the staff member may work on
    (None = all); a hotel outside it is refused with 403. A room's type must belong to the room's hotel."""

    # The most hotel/room-type combinations priced for one search
    MAX_SEARCH_CANDIDATES = 1000

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: RoomCreate, hotel_ids: Optional[Set[UUID]] = None) -> Room:
        """Create a room in a hotel. Raises 404 if the hotel or room type doesn't exist, 403 if the hotel isn't one
        of the caller's, 400 if the room type belongs to another hotel, 409 if the hotel already has this number."""
        if not (await self.db.execute(select(Hotel.id).where(Hotel.id == data.hotel_id))).first():
            raise APIException(status_code=404, message="Hotel not found")
        HotelService.ensure_access(data.hotel_id, hotel_ids)
        room_type_hotel = (await self.db.execute(select(RoomType.hotel_id).where(RoomType.id == data.room_type_id))).scalar_one_or_none()
        if room_type_hotel is None:
            raise APIException(status_code=404, message="Room type not found")
        if room_type_hotel != data.hotel_id:
            raise APIException(status_code=400, message="That room type belongs to another hotel")
        if (await self.db.execute(select(Room.id).where(Room.hotel_id == data.hotel_id, Room.number == data.number))).first():
            raise APIException(status_code=409, message=f"The hotel already has a room '{data.number}'")

        room_id = uuid7()
        room = Room(id=room_id, **data.model_dump())
        self.db.add(room)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests for the same number raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message=f"The hotel already has a room '{data.number}'")

        return await self.get(room_id)

    async def get(self, room_id: UUID) -> Room:
        """Get a room with its hotel, room type, capacity and standard price. Raises 404 if it doesn't exist."""
        row = (await self.db.execute(
            select(Room, Hotel.name, Hotel.currency, RoomType.name, RoomType.max_guests, RoomType.price_per_night)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(Room.id == room_id)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Room not found")
        room, hotel_name, currency, room_type_name, max_guests, price_per_night = row
        room.hotel_name, room.currency, room.room_type_name = hotel_name, currency, room_type_name
        room.max_guests, room.price_per_night = max_guests, price_per_night

        return room

    async def list(self, filter: RoomList, page: int = 1, limit: int = 10) -> dict:
        """Get a paginated, filtered list of rooms by hotel and number. With available_from/available_to, only rooms
        with no active booking overlapping those nights; with guests, only rooms that sleep that many.
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
        if filter.guests is not None:
            conditions.append(RoomType.max_guests >= filter.guests)
        if filter.available_from and filter.available_to:
            taken = select(Booking.room_id).where(
                Booking.status.in_(BookingService.ACTIVE_STATUSES),
                Booking.check_in < filter.available_to,
                Booking.check_out > filter.available_from,
            )
            conditions.append(Room.id.notin_(taken))

        total = (await self.db.execute(
            select(func.count()).select_from(Room).join(RoomType, Room.room_type_id == RoomType.id).where(*conditions)
        )).scalar() or 0
        rows = (await self.db.execute(
            select(Room, Hotel.name, Hotel.currency, RoomType.name, RoomType.max_guests, RoomType.price_per_night)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(*conditions)
            .order_by(Hotel.name, Room.number, Room.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )).all()

        rooms = []
        for room, hotel_name, currency, room_type_name, max_guests, price_per_night in rows:
            room.hotel_name, room.currency, room.room_type_name = hotel_name, currency, room_type_name
            room.max_guests, room.price_per_night = max_guests, price_per_night
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

    async def update(self, room_id: UUID, data: RoomUpdate, hotel_ids: Optional[Set[UUID]] = None) -> Room:
        """Update the fields that were sent (PATCH semantics).
        Raises 404 if the room or the new room type doesn't exist, 403 if the hotel isn't one of the caller's,
        400 if nothing was sent, a field is null, or the room type belongs to another hotel,
        409 if the number is already used in this hotel."""
        room = await self.get(room_id)
        HotelService.ensure_access(room.hotel_id, hotel_ids)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field, value in changes.items():
            if value is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        if "room_type_id" in changes:
            room_type_hotel = (await self.db.execute(
                select(RoomType.hotel_id).where(RoomType.id == changes["room_type_id"])
            )).scalar_one_or_none()
            if room_type_hotel is None:
                raise APIException(status_code=404, message="Room type not found")
            if room_type_hotel != room.hotel_id:
                raise APIException(status_code=400, message="That room type belongs to another hotel")
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

        return await self.get(room_id)

    async def delete(self, room_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> None:
        """Delete a room. Raises 404 if it doesn't exist, 403 if its hotel isn't one of the caller's,
        409 if it has bookings (past ones included)."""
        room = await self.get(room_id)
        HotelService.ensure_access(room.hotel_id, hotel_ids)

        bookings = (await self.db.execute(select(func.count()).select_from(Booking).where(Booking.room_id == room_id))).scalar()
        if bookings:
            raise APIException(status_code=409, message=f"The room has {bookings} booking(s) and can't be deleted")

        await self.db.delete(room)
        await self.db.commit()

    async def search(self, query: SearchQuery, page: int = 1, limit: int = 10) -> dict:
        """Room types that are free for the whole stay, with the stay's price night by night (seasonal and weekday rates,
        then the hotel's tax) and the hotel's rating. Each result is one room type at one hotel, with how many such
        rooms are free and one of them to book. Prices depend on per-night rules, so up to MAX_SEARCH_CANDIDATES
        combinations are priced before sorting and paging.
        Raises 400 if page is below 1 or limit is outside 1-100."""
        if page < 1:
            raise APIException(status_code=400, message="page must be 1 or greater")
        if not 1 <= limit <= 100:
            raise APIException(status_code=400, message="limit must be between 1 and 100")

        taken = select(Booking.room_id).where(
            Booking.status.in_(BookingService.ACTIVE_STATUSES),
            Booking.check_in < query.check_out,
            Booking.check_out > query.check_in,
        )
        conditions = [Room.id.notin_(taken), RoomType.max_guests >= query.guests]
        if query.city:
            conditions.append(Hotel.city.ilike(f"%{query.city}%"))
        if query.country:
            conditions.append(Hotel.country.ilike(f"%{query.country}%"))
        if query.hotel_id:
            conditions.append(Hotel.id == query.hotel_id)
        if query.currency:
            conditions.append(Hotel.currency == query.currency.value)
        if query.bed:
            conditions.append(RoomType.bed == query.bed.value)
        for amenity in query.amenities or []:
            conditions.append(getattr(RoomType, amenity).is_(True))  # names were checked against AMENITIES

        groups = (await self.db.execute(
            select(Hotel, RoomType, func.count(Room.id))
            .select_from(Room)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(*conditions)
            .group_by(Hotel.id, RoomType.id)
            .limit(self.MAX_SEARCH_CANDIDATES)
        )).all()

        rates: Dict[UUID, List[RoomRate]] = {}
        ratings: Dict[UUID, tuple] = {}
        if groups:
            type_ids = [room_type.id for _, room_type, _ in groups]
            for rate in (await self.db.execute(select(RoomRate).where(RoomRate.room_type_id.in_(type_ids)))).scalars():
                rates.setdefault(rate.room_type_id, []).append(rate)
            hotel_ids = list({hotel.id for hotel, _, _ in groups})
            for hotel_id, average, count in (await self.db.execute(
                select(Booking.hotel_id, func.avg(Review.rating), func.count(Review.id))
                .join(Review, Review.booking_id == Booking.id)
                .where(Booking.hotel_id.in_(hotel_ids), Review.is_approved.is_(True))
                .group_by(Booking.hotel_id)
            )).all():
                ratings[hotel_id] = (round(float(average), 2), count)

        nights = (query.check_out - query.check_in).days
        results = []
        for hotel, room_type, available in groups:
            price = RoomTypeService.price_stay(
                room_type.price_per_night, rates.get(room_type.id, []), query.check_in, query.check_out, 0, hotel.tax_percent
            )
            nightly_average = price["base_price"] // nights
            if query.min_price is not None and nightly_average < query.min_price:
                continue
            if query.max_price is not None and nightly_average > query.max_price:
                continue
            average_rating, review_count = ratings.get(hotel.id, (None, 0))
            results.append({
                "hotel": {
                    "id": hotel.id, "name": hotel.name, "city": hotel.city, "country": hotel.country,
                    "currency": hotel.currency, "average_rating": average_rating, "review_count": review_count,
                },
                "room_type": {
                    "id": room_type.id, "name": room_type.name, "bed": room_type.bed, "max_guests": room_type.max_guests,
                    "amenities": [a for a in AMENITIES if getattr(room_type, a)], "price_per_night": room_type.price_per_night,
                },
                "rooms_available": available,
                "nights": nights,
                "nightly_average": nightly_average,
                "base_price": price["base_price"],
                "tax_percent": price["tax_percent"],
                "tax_amount": price["tax_amount"],
                "total_price": price["total_price"],
                "currency": hotel.currency,
            })

        if query.sort == SearchSort.PRICE_DESC:
            results.sort(key=lambda r: (-r["total_price"], r["hotel"]["name"], r["room_type"]["name"]))
        elif query.sort == SearchSort.RATING:
            results.sort(key=lambda r: (-(r["hotel"]["average_rating"] or 0), r["total_price"], r["hotel"]["name"]))
        elif query.sort == SearchSort.NAME:
            results.sort(key=lambda r: (r["hotel"]["name"].lower(), r["room_type"]["name"].lower(), r["total_price"]))
        else:
            results.sort(key=lambda r: (r["total_price"], r["hotel"]["name"], r["room_type"]["name"]))

        total = len(results)
        page_results = results[(page - 1) * limit: page * limit]

        # One free room to book for each result on this page
        if page_results:
            pairs = [(r["hotel"]["id"], r["room_type"]["id"]) for r in page_results]
            first_free: Dict[tuple, UUID] = {}
            for hotel_id, room_type_id, room_id in (await self.db.execute(
                select(Room.hotel_id, Room.room_type_id, Room.id)
                .where(tuple_(Room.hotel_id, Room.room_type_id).in_(pairs), Room.id.notin_(taken))
                .order_by(Room.number, Room.id)
            )).all():
                first_free.setdefault((hotel_id, room_type_id), room_id)
            for r in page_results:
                r["room_id"] = first_free[(r["hotel"]["id"], r["room_type"]["id"])]

        return {
            "results": page_results,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit,
            },
        }
