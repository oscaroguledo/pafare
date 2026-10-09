from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import List, Optional
from datetime import date, datetime
from uuid import UUID

from models.bookings.room_type import BedType

# The yes/no features a room type can have. Used to validate the `amenities` filter.
AMENITIES = ('wardrobe', 'desk_chair', 'tv', 'wifi', 'safe', 'minifridge', 'kettle', 'coffee_maker', 'tea_maker', 'room_service', 'bottled_water', 'bathrobe', 'slippers', 'hairdryer', 'keycard', 'seating_area', 'bedside_lamp', 'soundproofing', 'jacuzzi', 'kitchenette')


class RoomTypeCreate(BaseModel):
    """A category of room at one hotel. `price_per_night` is the standard rate in minor units (e.g. kobo or cents) of the
    hotel's currency; seasonal and weekday prices are added as rates."""
    model_config = ConfigDict(str_strip_whitespace=True)

    hotel_id: UUID
    name: str = Field(min_length=1, max_length=100)
    bed: BedType
    max_guests: int = Field(2, ge=1, le=20)
    windows: int = Field(0, ge=0)
    doors: int = Field(0, ge=0)
    wardrobe: bool = False
    desk_chair: bool = False
    tv: bool = False
    wifi: bool = False
    safe: bool = False
    minifridge: bool = False
    kettle: bool = False
    coffee_maker: bool = False
    tea_maker: bool = False
    room_service: bool = False
    bottled_water: bool = False
    bathrobe: bool = False
    slippers: bool = False
    hairdryer: bool = False
    keycard: bool = False
    seating_area: bool = False
    bedside_lamp: bool = False
    soundproofing: bool = False
    jacuzzi: bool = False
    kitchenette: bool = False
    price_per_night: int = Field(0, ge=0)


class RoomTypeUpdate(BaseModel):
    """Fields that can change; all optional. None of them can be set to null."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    bed: Optional[BedType] = None
    max_guests: Optional[int] = Field(None, ge=1, le=20)
    windows: Optional[int] = Field(None, ge=0)
    doors: Optional[int] = Field(None, ge=0)
    wardrobe: Optional[bool] = None
    desk_chair: Optional[bool] = None
    tv: Optional[bool] = None
    wifi: Optional[bool] = None
    safe: Optional[bool] = None
    minifridge: Optional[bool] = None
    kettle: Optional[bool] = None
    coffee_maker: Optional[bool] = None
    tea_maker: Optional[bool] = None
    room_service: Optional[bool] = None
    bottled_water: Optional[bool] = None
    bathrobe: Optional[bool] = None
    slippers: Optional[bool] = None
    hairdryer: Optional[bool] = None
    keycard: Optional[bool] = None
    seating_area: Optional[bool] = None
    bedside_lamp: Optional[bool] = None
    soundproofing: Optional[bool] = None
    jacuzzi: Optional[bool] = None
    kitchenette: Optional[bool] = None
    price_per_night: Optional[int] = Field(None, ge=0)


class RoomTypeList(BaseModel):
    """Filters for the room type list; every field is optional and they combine with AND."""
    hotel_id: Optional[UUID] = None
    search: Optional[str] = None
    bed: Optional[BedType] = None
    min_guests: Optional[int] = Field(None, ge=1)   # room types that sleep at least this many
    min_price: Optional[int] = Field(None, ge=0)
    max_price: Optional[int] = Field(None, ge=0)
    amenities: Optional[List[str]] = None   # every listed amenity must be present, e.g. ["wifi", "tv"]

    @field_validator("amenities")
    @classmethod
    def known_amenities(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        unknown = sorted(set(value) - set(AMENITIES))
        if unknown:
            raise ValueError(f"Unknown amenities: {unknown}")
        return value


class RoomTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hotel_id: UUID
    name: str
    bed: BedType
    max_guests: int
    windows: int
    doors: int
    wardrobe: bool
    desk_chair: bool
    tv: bool
    wifi: bool
    safe: bool
    minifridge: bool
    kettle: bool
    coffee_maker: bool
    tea_maker: bool
    room_service: bool
    bottled_water: bool
    bathrobe: bool
    slippers: bool
    hairdryer: bool
    keycard: bool
    seating_area: bool
    bedside_lamp: bool
    soundproofing: bool
    jacuzzi: bool
    kitchenette: bool
    price_per_night: int
    created_at: datetime
    updated_at: Optional[datetime]


# ---- price rules ----

def _check_weekdays(value: Optional[List[int]]) -> Optional[List[int]]:
    if value is None:
        return value
    if not value or any(day < 0 or day > 6 for day in value):
        raise ValueError("weekdays must be numbers from 0 (Monday) to 6 (Sunday)")
    return sorted(set(value))


class RoomRateCreate(BaseModel):
    """A price rule for a room type. Set dates, weekdays or both; leave a field out to mean 'always'. A night uses
    the matching rule with the highest `priority` (the newest wins a tie), or the standard price if none matches."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    price_per_night: int = Field(ge=0)               # minor units
    start_date: Optional[date] = None                # first night it applies to
    end_date: Optional[date] = None                  # last night it applies to
    weekdays: Optional[List[int]] = None             # 0 = Monday ... 6 = Sunday, e.g. [4, 5] for Friday and Saturday
    priority: int = Field(0, ge=0, le=1000)

    _weekdays = field_validator("weekdays")(_check_weekdays)

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class RoomRateUpdate(BaseModel):
    """Fields that can change; all optional. Set a date or weekdays field to null to clear it."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    price_per_night: Optional[int] = Field(None, ge=0)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    weekdays: Optional[List[int]] = None
    priority: Optional[int] = Field(None, ge=0, le=1000)

    _weekdays = field_validator("weekdays")(_check_weekdays)


class RoomRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_type_id: UUID
    name: str
    price_per_night: int
    start_date: Optional[date]
    end_date: Optional[date]
    weekdays: Optional[List[int]]
    priority: int
    created_at: datetime
    updated_at: Optional[datetime]

    @field_validator("weekdays", mode="before")
    @classmethod
    def weekdays_from_text(cls, value):
        # stored as "4,5"; shown as [4, 5]
        if isinstance(value, str):
            return [int(part) for part in value.split(",")] if value else None
        return value
