from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from models.base import BaseModel
from enum import Enum


class BedType(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"
    QUEEN = "queen"
    KING = "king"
    TWIN = "twin"
    OTHER = "other"


class RoomType(BaseModel):
    """A category of room (e.g. 'Deluxe King') that rooms point to."""
    __tablename__ = "room_types"
    __table_args__ = {'schema': 'bookings'}

    name: Mapped[str] = mapped_column(String(100), unique=True)
    bed: Mapped[BedType] = mapped_column(String(20), index=True)
    windows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    doors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wardrobe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    desk_chair: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    wifi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minifridge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kettle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    coffee_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tea_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    room_service: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bottled_water: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bathrobe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    slippers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hairdryer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    keycard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seating_area: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bedside_lamp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    soundproofing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    jacuzzi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kitchenette: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price_per_night: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)  # minor units (e.g. cents)


