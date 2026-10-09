from decimal import Decimal
from typing import Optional

from sqlalchemy import Integer, Date, ForeignKey, CheckConstraint, String, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from core.db import GUID, UTCDateTime
from models.base import BaseModel, Currency
from datetime import date, datetime
from enum import Enum
import uuid


class BookingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class Booking(BaseModel):
    """A guest's reservation of one room for a date range."""
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint('check_out > check_in', name='ck_bookings_dates'),
        CheckConstraint('guests > 0', name='ck_bookings_guests'),
        CheckConstraint('discount BETWEEN 0 AND 100', name='ck_bookings_discount'),
        CheckConstraint('tax_percent BETWEEN 0 AND 100', name='ck_bookings_tax_percent'),
        CheckConstraint('cancellation_fee >= 0 AND refund_amount >= 0', name='ck_bookings_cancellation_amounts'),
        {'schema': 'bookings'},
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.hotels.id", ondelete="RESTRICT"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id", ondelete="RESTRICT"), index=True)
    room_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.rooms.id", ondelete="RESTRICT"), index=True)
    check_in: Mapped[date] = mapped_column(Date, index=True)
    check_out: Mapped[date] = mapped_column(Date, index=True)
    check_in_time: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)  # HH:MM
    check_out_time: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)  # HH:MM
    guests: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[BookingStatus] = mapped_column(String(20), default=BookingStatus.PENDING, index=True)
    # All money is in minor units of `currency`. total = base_price - discount% + tax_amount.
    base_price: Mapped[int] = mapped_column(Integer, default=0)   # the nights added up at their rates, before discount and tax
    discount: Mapped[int] = mapped_column(Integer, default=0)  # percentage, 0-100 (see ck_bookings_discount)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)  # the hotel's rate when the booking was made
    tax_amount: Mapped[int] = mapped_column(Integer, default=0)
    total_price: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    cancellation_fee: Mapped[int] = mapped_column(Integer, default=0)  # what the hotel kept for a late cancellation
    refund_amount: Mapped[int] = mapped_column(Integer, default=0)     # what was handed back when cancelled
    booking_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, unique=True, index=True)
    currency: Mapped[Currency] = mapped_column(String(10), default=Currency.NGN, index=True)