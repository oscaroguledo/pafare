from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import List, Optional
from datetime import date, datetime
from uuid import UUID

from models.base import Currency
from models.bookings.room_type import BedType
from schemas.bookings.room_type import AMENITIES


class RoomCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    hotel_id: UUID
    room_type_id: UUID
    number: str = Field(min_length=1, max_length=20)


class RoomUpdate(BaseModel):
    """Fields that can change; all optional. A room never moves to another hotel."""
    model_config = ConfigDict(str_strip_whitespace=True)

    room_type_id: Optional[UUID] = None
    number: Optional[str] = Field(None, min_length=1, max_length=20)


class RoomList(BaseModel):
    """Filters for the room list. Give `available_from` and `available_to` together to get only rooms
    with no active booking overlapping those nights."""
    hotel_id: Optional[UUID] = None
    room_type_id: Optional[UUID] = None
    search: Optional[str] = None            # matches the room number
    guests: Optional[int] = Field(None, ge=1)   # only rooms that sleep at least this many
    available_from: Optional[date] = None   # first night
    available_to: Optional[date] = None     # check-out day

    @model_validator(mode="after")
    def both_or_neither(self):
        if (self.available_from is None) != (self.available_to is None):
            raise ValueError("available_from and available_to must be given together")
        if self.available_from and self.available_to <= self.available_from:
            raise ValueError("available_to must be after available_from")
        return self


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hotel_id: UUID
    hotel_name: str
    room_type_id: UUID
    room_type_name: str
    max_guests: int
    price_per_night: int
    currency: str
    number: str
    created_at: datetime
    updated_at: Optional[datetime]


# ---- search ----

class SearchSort(str, Enum):
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    RATING = "rating"
    NAME = "name"


class SearchQuery(BaseModel):
    """Find rooms to book. Every filter is optional except the dates and the guest count, which decide availability.
    Prices are totals for the whole stay in the hotel's currency, so compare hotels in the same currency."""
    check_in: date
    check_out: date
    guests: int = Field(1, ge=1, le=20)
    city: Optional[str] = None
    country: Optional[str] = None
    hotel_id: Optional[UUID] = None
    bed: Optional[BedType] = None
    amenities: Optional[List[str]] = None       # every listed amenity must be present, e.g. ["wifi", "tv"]
    currency: Optional[Currency] = None
    min_price: Optional[int] = Field(None, ge=0)    # per night on average, minor units, before tax
    max_price: Optional[int] = Field(None, ge=0)
    sort: SearchSort = SearchSort.PRICE_ASC

    @field_validator("amenities")
    @classmethod
    def known_amenities(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        unknown = sorted(set(value) - set(AMENITIES))
        if unknown:
            raise ValueError(f"Unknown amenities: {unknown}")
        return value

    @model_validator(mode="after")
    def valid_stay(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if (self.check_out - self.check_in).days > 90:
            raise ValueError("A stay can be at most 90 nights")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be above max_price")
        return self


class SearchHotel(BaseModel):
    id: UUID
    name: str
    city: Optional[str]
    country: Optional[str]
    currency: Currency
    average_rating: Optional[float]   # from approved reviews; null when there are none
    review_count: int


class SearchRoomType(BaseModel):
    id: UUID
    name: str
    bed: BedType
    max_guests: int
    amenities: List[str]              # the ones this room type has
    price_per_night: int              # the standard rate; the stay below uses any seasonal or weekday rates


class SearchResult(BaseModel):
    """One room type at one hotel that is free for the whole stay."""
    hotel: SearchHotel
    room_type: SearchRoomType
    rooms_available: int
    room_id: UUID                     # a free room of this type; pass it to POST /bookings/ to book
    nights: int
    nightly_average: int              # base_price / nights
    base_price: int
    tax_percent: Decimal
    tax_amount: int
    total_price: int
    currency: Currency
