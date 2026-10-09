from sqlalchemy import Boolean, ForeignKey, Text, Integer, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from core.db import GUID
from models.base import BaseModel
from typing import Optional
import uuid


class Review(BaseModel):
    """A guest's review of a stay; one per booking (the hotel and guest come from the booking)."""
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint('rating BETWEEN 1 AND 5', name='ck_reviews_rating'),
        {'schema': 'bookings'},
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.bookings.id", ondelete="CASCADE"), unique=True)
    rating: Mapped[int] = mapped_column(Integer, index=True)  # 1-5 stars
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
