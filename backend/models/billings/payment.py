from sqlalchemy import String, Integer, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from core.db import GUID, UTCDateTime
from models.base import BaseModel, Currency
from datetime import datetime
from enum import Enum
import uuid
from typing import Optional


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class Payment(BaseModel):
    """A payment made towards a booking. Money records are kept: deleting a user or booking is refused while
    payments point at it."""
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint('amount >= 0', name='ck_payments_amount'),
        {'schema': 'billings'},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id", ondelete="RESTRICT"), index=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.bookings.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[int] = mapped_column(Integer)  # minor units (e.g. kobo or cents), in `currency`
    currency: Mapped[Currency] = mapped_column(String(3), default=Currency.NGN.value)
    status: Mapped[PaymentStatus] = mapped_column(String(20), default=PaymentStatus.PENDING.value, index=True)
    reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)  # the payment provider's transaction id
    card_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # the provider's token for the card, never the card number
    paid_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
