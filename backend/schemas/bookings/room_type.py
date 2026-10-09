from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Optional
from datetime import datetime
from uuid import UUID

from models.bookings.room_type import BedType

# The yes/no features a room type can have. Used to validate the `amenities` filter.
AMENITIES = ('wardrobe', 'desk_chair', 'tv', 'wifi', 'safe', 'minifridge', 'kettle', 'coffee_maker', 'tea_maker', 'room_service', 'bottled_water', 'bathrobe', 'slippers', 'hairdryer', 'keycard', 'seating_area', 'bedside_lamp', 'soundproofing', 'jacuzzi', 'kitchenette')


class RoomTypeCreate(BaseModel):
    """A category of room. `price_per_night` is in minor units (e.g. kobo or cents) of the hotel's currency."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    bed: BedType
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
    search: Optional[str] = None
    bed: Optional[BedType] = None
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
    name: str
    bed: BedType
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
