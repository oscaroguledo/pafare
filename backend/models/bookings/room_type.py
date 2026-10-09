import uuid
from datetime import date
from typing import Optional
from sqlalchemy import String, Integer, Boolean, Date, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from core.db import GUID
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
    """A category of room at one hotel (e.g. 'Deluxe King') that its rooms point to. Capacity, amenities and price
    belong to the hotel, so two hotels can both have a 'Deluxe King' with different prices."""
    __tablename__ = "room_types"
    __table_args__ = (
        # A name is unique within its hotel; the leading hotel_id also serves "room types of a hotel" lookups
        UniqueConstraint('hotel_id', 'name', name='uq_room_types_hotel_name'),
        CheckConstraint('max_guests >= 1', name='ck_room_types_max_guests'),
        CheckConstraint('price_per_night >= 0', name='ck_room_types_price'),
        {'schema': 'bookings'},
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.hotels.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    bed: Mapped[BedType] = mapped_column(String(20), index=True)
    max_guests: Mapped[int] = mapped_column(Integer, nullable=False, default=2, index=True)
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
    price_per_night: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)  # the standard rate, minor units (e.g. cents)


class RoomRate(BaseModel):
    """A price rule for a room type: a different nightly price for some dates and/or days of the week (a weekend
    rate, a Christmas rate). A night uses the matching rule with the highest priority (the newest wins a tie), or
    the room type's standard price if no rule matches."""
    __tablename__ = "room_rates"
    __table_args__ = (
        CheckConstraint('price_per_night >= 0', name='ck_room_rates_price'),
        CheckConstraint('end_date IS NULL OR start_date IS NULL OR end_date >= start_date', name='ck_room_rates_dates'),
        {'schema': 'bookings'},
    )

    room_type_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.room_types.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)   # first night it applies to; null = no start
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)     # last night it applies to; null = no end
    weekdays: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # e.g. "4,5" (Mon=0 ... Sun=6); null = every day
    price_per_night: Mapped[int] = mapped_column(Integer)                     # minor units
    priority: Mapped[int] = mapped_column(Integer, default=0)
