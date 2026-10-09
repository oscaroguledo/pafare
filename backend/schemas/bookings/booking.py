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
    total_price: int
    currency: Currency
    created_at: datetime
    updated_at: Optional[datetime]
