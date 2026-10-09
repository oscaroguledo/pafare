from decimal import Decimal
from sqlalchemy import String, Integer, Numeric, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from models.base import BaseModel, Currency
from typing import Optional
from models.accounts.user import HotelStaff


class Hotel(BaseModel):
    """A hotel property that owns room types and rooms."""
    __tablename__ = "hotels"
    __table_args__ = (
        CheckConstraint('tax_percent BETWEEN 0 AND 100', name='ck_hotels_tax_percent'),
        CheckConstraint('free_cancellation_hours >= 0', name='ck_hotels_free_cancellation_hours'),
        CheckConstraint('late_cancellation_fee_percent BETWEEN 0 AND 100', name='ck_hotels_late_cancellation_fee'),
        {'schema': 'bookings'},
    )

    name: Mapped[str] = mapped_column(String(255), index=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    zip_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    currency: Mapped[Currency] = mapped_column(String(3), default=Currency.NGN, index=True)

    # Tax added on top of the room price (e.g. 7.50 for 7.5% VAT)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    # Cancellation policy: free until this many hours before check-in; after that the hotel keeps this share of the total
    free_cancellation_hours: Mapped[int] = mapped_column(Integer, default=48)
    late_cancellation_fee_percent: Mapped[int] = mapped_column(Integer, default=100)
