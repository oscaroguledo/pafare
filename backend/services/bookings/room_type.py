from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Set
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from uuid import UUID
from core.utils.uuid_utils import uuid7
from models.bookings.hotel import Hotel
from models.bookings.room import Room
from models.bookings.room_type import RoomRate
from models.bookings.room_type import RoomType
from core.exceptions import APIException
from schemas.bookings.room_type import RoomRateCreate, RoomRateUpdate, RoomTypeCreate, RoomTypeUpdate, RoomTypeList
from services.bookings.hotel import HotelService
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)


class RoomTypeService:
    """Room types, their price rules (seasonal and weekday rates) and the pricing of stays. A room type belongs to one
    hotel. Reading is public. Methods that change one take `hotel_ids`, the hotels the staff member may work on
    (None = all); a hotel outside it is refused with 403."""

    MAX_NIGHTS = 90

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: RoomTypeCreate, hotel_ids: Optional[Set[UUID]] = None) -> RoomType:
        """Create a room type at a hotel. Raises 404 if the hotel doesn't exist, 403 if it isn't one of the caller's,
        409 if the hotel already has a room type with this name (case-insensitive)."""
        if not (await self.db.execute(select(Hotel.id).where(Hotel.id == data.hotel_id))).first():
            raise APIException(status_code=404, message="Hotel not found")
        HotelService.ensure_access(data.hotel_id, hotel_ids)
        if (await self.db.execute(
            select(RoomType.id).where(RoomType.hotel_id == data.hotel_id, func.lower(RoomType.name) == data.name.lower())
        )).first():
            raise APIException(status_code=409, message=f"This hotel already has a room type '{data.name}'")

        room_type = RoomType(id=uuid7(), **{**data.model_dump(), "bed": data.bed.value})
        self.db.add(room_type)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests for the same name raced past the check above; the unique constraint caught it
            await self.db.rollback()
            raise APIException(status_code=409, message=f"This hotel already has a room type '{data.name}'")
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
        if filter.hotel_id:
            conditions.append(RoomType.hotel_id == filter.hotel_id)
        if filter.min_guests is not None:
            conditions.append(RoomType.max_guests >= filter.min_guests)
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

    async def update(self, room_type_id: UUID, data: RoomTypeUpdate, hotel_ids: Optional[Set[UUID]] = None) -> RoomType:
        """Update the fields that were sent (PATCH semantics). A new price applies to future bookings only.
        Raises 404 if the room type doesn't exist, 403 if its hotel isn't one of the caller's, 400 if nothing was
        sent or a field is set to null, 409 if the new name belongs to another room type of the same hotel."""
        room_type = await self.get(room_type_id)
        HotelService.ensure_access(room_type.hotel_id, hotel_ids)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field, value in changes.items():
            if value is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        if "name" in changes:
            taken = (await self.db.execute(
                select(RoomType.id).where(
                    RoomType.hotel_id == room_type.hotel_id,
                    func.lower(RoomType.name) == changes["name"].lower(),
                    RoomType.id != room_type_id,
                )
            )).first()
            if taken:
                raise APIException(status_code=409, message=f"This hotel already has a room type '{changes['name']}'")
        if "bed" in changes:
            changes["bed"] = changes["bed"].value

        for field, value in changes.items():
            setattr(room_type, field, value)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise APIException(status_code=409, message="This hotel already has a room type with that name")
        await self.db.refresh(room_type)

        return room_type

    async def delete(self, room_type_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> None:
        """Delete a room type and its price rules. Raises 404 if it doesn't exist, 403 if its hotel isn't one of the
        caller's, 409 if rooms still use it."""
        room_type = await self.get(room_type_id)
        HotelService.ensure_access(room_type.hotel_id, hotel_ids)

        rooms = (await self.db.execute(select(func.count()).select_from(Room).where(Room.room_type_id == room_type_id))).scalar()
        if rooms:
            raise APIException(status_code=409, message=f"Room type is still used by {rooms} room(s)")

        await self.db.delete(room_type)
        await self.db.commit()

    # ---- price rules ----

    async def create_rate(self, room_type_id: UUID, data: RoomRateCreate, hotel_ids: Optional[Set[UUID]] = None) -> RoomRate:
        """Add a price rule to a room type. Raises 404 if the room type doesn't exist, 403 if its hotel isn't one
        of the caller's."""
        room_type = await self.get(room_type_id)
        HotelService.ensure_access(room_type.hotel_id, hotel_ids)

        values = data.model_dump()
        values["weekdays"] = ",".join(str(day) for day in data.weekdays) if data.weekdays is not None else None
        rate = RoomRate(id=uuid7(), room_type_id=room_type_id, **values)
        self.db.add(rate)
        await self.db.commit()
        await self.db.refresh(rate)

        return rate

    async def get_rate(self, rate_id: UUID, room_type_id: Optional[UUID] = None) -> RoomRate:
        """Get a price rule (of this room type, when room_type_id is given). Raises 404 if it doesn't exist."""
        conditions = [RoomRate.id == rate_id]
        if room_type_id is not None:
            conditions.append(RoomRate.room_type_id == room_type_id)
        rate = (await self.db.execute(select(RoomRate).where(*conditions))).scalar_one_or_none()
        if not rate:
            raise APIException(status_code=404, message="Price rule not found")

        return rate

    async def list_rates(self, room_type_id: UUID) -> List[RoomRate]:
        """A room type's price rules, highest priority first. Raises 404 if the room type doesn't exist."""
        await self.get(room_type_id)

        return list((await self.db.execute(
            select(RoomRate)
            .where(RoomRate.room_type_id == room_type_id)
            .order_by(RoomRate.priority.desc(), RoomRate.created_at.desc(), RoomRate.id)
        )).scalars().all())

    async def update_rate(
        self, room_type_id: UUID, rate_id: UUID, data: RoomRateUpdate, hotel_ids: Optional[Set[UUID]] = None,
    ) -> RoomRate:
        """Update the fields that were sent (PATCH semantics); a date or weekdays field set to null is cleared.
        A new price applies to future bookings only.
        Raises 404 if the room type or rule doesn't exist, 403 if its hotel isn't one of the caller's, 400 if nothing
        was sent, a required field is null, or the end date would be before the start date."""
        room_type = await self.get(room_type_id)
        HotelService.ensure_access(room_type.hotel_id, hotel_ids)
        rate = await self.get_rate(rate_id, room_type_id)

        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise APIException(status_code=400, message="No fields to update")
        for field in ("name", "price_per_night", "priority"):
            if field in changes and changes[field] is None:
                raise APIException(status_code=400, message=f"{field} cannot be null")

        start: Optional[date] = changes.get("start_date", rate.start_date)
        end: Optional[date] = changes.get("end_date", rate.end_date)
        if start and end and end < start:
            raise APIException(status_code=400, message="end_date cannot be before start_date")
        if "weekdays" in changes:
            changes["weekdays"] = ",".join(str(day) for day in changes["weekdays"]) if changes["weekdays"] is not None else None

        for field, value in changes.items():
            setattr(rate, field, value)
        await self.db.commit()
        await self.db.refresh(rate)

        return rate

    async def delete_rate(self, room_type_id: UUID, rate_id: UUID, hotel_ids: Optional[Set[UUID]] = None) -> None:
        """Delete a price rule; future bookings use the next matching rule or the standard price.
        Raises 404 if the room type or rule doesn't exist, 403 if its hotel isn't one of the caller's."""
        room_type = await self.get(room_type_id)
        HotelService.ensure_access(room_type.hotel_id, hotel_ids)
        rate = await self.get_rate(rate_id, room_type_id)

        await self.db.delete(rate)
        await self.db.commit()

    # ---- pricing ----

    @staticmethod
    def parse_weekdays(text: Optional[str]) -> Optional[Set[int]]:
        """'4,5' -> {4, 5} (Monday is 0, Sunday is 6); None -> every day."""
        if text is None or text == "":
            return None
        return {int(part) for part in text.split(",")}

    @staticmethod
    def money(value) -> int:
        """Round a money amount to a whole number of minor units, halves up."""
        return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def rate_for_night(base_price: int, rates: List[RoomRate], night: date) -> tuple:
        """(price, rate name) for one night: the matching rule with the highest priority (newest wins a tie), or the
        standard price and None when no rule matches."""
        best, best_key = None, None
        for rate in rates:
            if rate.start_date and night < rate.start_date:
                continue
            if rate.end_date and night > rate.end_date:
                continue
            weekdays = RoomTypeService.parse_weekdays(rate.weekdays)
            if weekdays is not None and night.weekday() not in weekdays:
                continue
            key = (rate.priority, rate.created_at, str(rate.id))
            if best is None or key > best_key:
                best, best_key = rate, key
        return (best.price_per_night, best.name) if best else (base_price, None)

    @staticmethod
    def price_stay(base_price: int, rates: List[RoomRate], check_in: date, check_out: date,
                   discount: int = 0, tax_percent=0) -> Dict:
        """Price a stay night by night. The discount (percent) comes off the nights' total, then tax is added to what is
        left. Returns the nights, base_price (before discount and tax), tax_amount and total_price."""
        nights = []
        day = check_in
        while day < check_out:
            price, rate_name = RoomTypeService.rate_for_night(base_price, rates, day)
            nights.append({"night": day, "price": price, "rate_name": rate_name})
            day += timedelta(days=1)

        subtotal = sum(n["price"] for n in nights)
        after_discount = RoomTypeService.money(Decimal(subtotal) * (100 - discount) / 100)
        tax_amount = RoomTypeService.money(Decimal(after_discount) * Decimal(tax_percent) / 100)
        return {
            "nights": nights,
            "base_price": subtotal,
            "discount": discount,
            "tax_percent": Decimal(tax_percent),
            "tax_amount": tax_amount,
            "total_price": after_discount + tax_amount,
        }

    async def stay_price(self, room_type: RoomType, hotel: Hotel, check_in: date, check_out: date, discount: int = 0) -> Dict:
        """Price a stay of this room type at this hotel: its price rules, then the hotel's tax. See price_stay."""
        rates = list((await self.db.execute(select(RoomRate).where(RoomRate.room_type_id == room_type.id))).scalars().all())
        return self.price_stay(room_type.price_per_night, rates, check_in, check_out, discount, hotel.tax_percent)

    async def quote(self, room_id: UUID, check_in: date, check_out: date, guests: int = 1) -> Dict:
        """The price of a stay in a room, night by night, with tax. Raises 404 if the room doesn't exist, 400 if the
        dates are wrong, the stay is longer than 90 nights or the room is too small for the guests."""
        if check_out <= check_in:
            raise APIException(status_code=400, message="check_out must be after check_in")
        if (check_out - check_in).days > self.MAX_NIGHTS:
            raise APIException(status_code=400, message=f"A stay can be at most {self.MAX_NIGHTS} nights")
        row = (await self.db.execute(
            select(Room, Hotel, RoomType)
            .join(Hotel, Room.hotel_id == Hotel.id)
            .join(RoomType, Room.room_type_id == RoomType.id)
            .where(Room.id == room_id)
        )).first()
        if not row:
            raise APIException(status_code=404, message="Room not found")
        room, hotel, room_type = row
        if guests > room_type.max_guests:
            raise APIException(status_code=400, message=f"This room sleeps at most {room_type.max_guests} guests")

        quote = await self.stay_price(room_type, hotel, check_in, check_out)
        return {**quote, "room_id": room.id, "currency": hotel.currency, "max_guests": room_type.max_guests}
