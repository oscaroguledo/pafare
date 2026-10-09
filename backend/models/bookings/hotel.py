from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from models.base import BaseModel, Currency
from typing import Optional


class Hotel(BaseModel):
    """A hotel property that owns rooms."""
    __tablename__ = "hotels"
    __table_args__ = {'schema': 'bookings'}

    name: Mapped[str] = mapped_column(String(255), index=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    zip_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    currency: Mapped[Currency] = mapped_column(String(3), default=Currency.NGN, index=True)
