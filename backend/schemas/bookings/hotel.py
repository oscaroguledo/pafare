from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime
from uuid import UUID

from models.base import Currency


class HotelCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    zip_code: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    currency: Currency = Currency.NGN


class HotelUpdate(BaseModel):
    """Fields that can change; all optional. `name` and `currency` cannot be set to null."""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    zip_code: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    currency: Optional[Currency] = None


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
    created_at: datetime
    updated_at: Optional[datetime]
