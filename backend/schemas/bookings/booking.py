from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator
from typing import Optional
from datetime import date, datetime
from uuid import UUID

from models.base import Currency
from models.bookings.booking import BookingStatus


class BookingCreate(BaseModel):
    """What a guest sends. Hotel, prices, currency, status and the booking code are set by the server."""
    room_id: UUID
    check_in: date
    check_out: date
    guests: int = Field(1, ge=1)
    check_in_time: Optional[AwareDatetime] = None
    check_out_time: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if (self.check_out - self.check_in).days > 90:
            raise ValueError("A stay can be at most 90 nights")
        return self


class BookingUpdate(BaseModel):
    """Reschedule or change the party size; all optional. The price is recalculated if the dates change."""
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    guests: Optional[int] = Field(None, ge=1)
    check_in_time: Optional[AwareDatetime] = None
    check_out_time: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class BookingDiscount(BaseModel):
    discount: int = Field(ge=0, le=100)   # percent


class BookingStatusChange(BaseModel):
    status: BookingStatus


class BookingCancel(BaseModel):
    """Staff may waive the late-cancellation fee. For a guest's own cancellation this is ignored."""
    waive_fee: bool = False


class NightPrice(BaseModel):
    night: date
    price: int
    rate_name: Optional[str]       # the price rule used, or null for the standard price


class BookingQuote(BaseModel):
    """What a stay costs, night by night. All money is in minor units of `currency`."""
    room_id: UUID
    currency: Currency
    max_guests: int
    nights: list[NightPrice]
    base_price: int
    tax_percent: Decimal
    tax_amount: int
    total_price: int


class CancellationQuote(BaseModel):
    """What cancelling a booking would cost and return right now."""
    booking_id: UUID
    currency: Currency
    hours_until_check_in: int
    free_until: datetime
    within_free_window: bool
    paid: int               # net of any refunds so far
    fee: int                # what the hotel would keep
    refund: int             # what would be handed back


class BookingList(BaseModel):
    """Filters for the booking list; every field is optional and they combine with AND."""
    user_id: Optional[UUID] = None
    hotel_id: Optional[UUID] = None
    room_id: Optional[UUID] = None
    status: Optional[BookingStatus] = None
    code: Optional[str] = None                 # booking code, exact match (case-insensitive)
    check_in_from: Optional[date] = None
    check_in_to: Optional[date] = None


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_code: Optional[str]
    hotel_id: UUID
    hotel_name: str
    user_id: UUID
    room_id: UUID
    room_number: str
    room_type_name: str
    check_in: date
    check_out: date
    check_in_time: Optional[datetime]
    check_out_time: Optional[datetime]
    guests: int
    status: BookingStatus
    base_price: int
    discount: int
    tax_percent: Decimal
    tax_amount: int
    total_price: int
    currency: Currency
    cancelled_at: Optional[datetime]
    cancellation_fee: int
    refund_amount: int
    created_at: datetime
    updated_at: Optional[datetime]
