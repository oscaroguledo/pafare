from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime
from uuid import UUID

from models.base import Currency
from models.billings.payment import PaymentStatus
from models.bookings.booking import BookingStatus


class PaymentCreate(BaseModel):
    """Pay towards your own booking. The currency is the booking's. Leave `amount` out to pay everything still
    owed; give it (in minor units) for a part payment."""
    model_config = ConfigDict(str_strip_whitespace=True)

    booking_id: UUID
    amount: Optional[int] = Field(None, ge=1)
    card_id: Optional[str] = Field(None, max_length=100)   # the payment provider's token for the card, never the card number


class PaymentConfirm(BaseModel):
    """The payment provider's transaction id, if known."""
    model_config = ConfigDict(str_strip_whitespace=True)

    reference: Optional[str] = Field(None, min_length=1, max_length=100)


class PaymentList(BaseModel):
    """Filters for the payment list; every field is optional and they combine with AND."""
    user_id: Optional[UUID] = None
    booking_id: Optional[UUID] = None
    status: Optional[PaymentStatus] = None


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_id: UUID
    booking_code: Optional[str]
    booking_status: BookingStatus   # a pending booking becomes confirmed when its payments cover the total
    user_id: UUID
    amount: int
    currency: Currency
    status: PaymentStatus
    reference: Optional[str]
    card_id: Optional[str]
    paid_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]


class PaymentSummary(BaseModel):
    """What a booking costs and how much of it is settled. All amounts are in minor units of `currency`."""
    booking_id: UUID
    currency: Currency
    total: int         # the booking's total price
    paid: int          # succeeded payments
    pending: int       # payments started but not confirmed yet
    remaining: int     # total - paid - pending: what can still be paid
