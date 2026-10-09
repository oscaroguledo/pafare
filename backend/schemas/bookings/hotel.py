from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime
from uuid import UUID

from models.base import Currency


class HotelCreate(BaseModel):
    """`tax_percent` is added on top of room prices (7.5 = 7.5% VAT). Cancellation is free until
    `free_cancellation_hours` before check-in; after that the hotel keeps `late_cancellation_fee_percent` of the total."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    zip_code: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    currency: Currency = Currency.NGN
    tax_percent: Decimal = Field(Decimal("0"), ge=0, le=100, decimal_places=2)
    free_cancellation_hours: int = Field(48, ge=0, le=8760)
    late_cancellation_fee_percent: int = Field(100, ge=0, le=100)


class HotelUpdate(BaseModel):
    """Fields that can change; all optional. `name`, `currency` and the tax and cancellation fields cannot be set to
    null. A new tax rate or policy applies to future bookings only."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    zip_code: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    currency: Optional[Currency] = None
    tax_percent: Optional[Decimal] = Field(None, ge=0, le=100, decimal_places=2)
    free_cancellation_hours: Optional[int] = Field(None, ge=0, le=8760)
    late_cancellation_fee_percent: Optional[int] = Field(None, ge=0, le=100)


class HotelList(BaseModel):
    """Filters for the hotel list; every field is optional and they combine with AND."""
    search: Optional[str] = None      # matches name, city or country
    city: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[Currency] = None


class HotelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip_code: Optional[str]
    country: Optional[str]
    currency: Currency
    tax_percent: Decimal
    free_cancellation_hours: int
    late_cancellation_fee_percent: int
    created_at: datetime
    updated_at: Optional[datetime]


class HotelStaffAssign(BaseModel):
    user_id: UUID


class HotelStaffResponse(BaseModel):
    user_id: UUID
    email: str
    firstname: str
    lastname: str
    role_name: str
    assigned_at: datetime
