from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime
from uuid import UUID


class ReviewCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    booking_id: UUID
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=2000)


class ReviewUpdate(BaseModel):
    """Fields that can change; all optional. Any edit sends the review back for approval."""
    model_config = ConfigDict(str_strip_whitespace=True)

    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=2000)


class ReviewModeration(BaseModel):
    is_approved: bool


class ReviewList(BaseModel):
    """Filters for the review list; every field is optional and they combine with AND."""
    hotel_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    is_approved: Optional[bool] = None


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_id: UUID
    hotel_id: UUID
    hotel_name: str
    user_id: UUID
    rating: int
    comment: Optional[str]
    is_approved: bool
    created_at: datetime
    updated_at: Optional[datetime]


class HotelRating(BaseModel):
    """Average and count of a hotel's approved reviews."""
    hotel_id: UUID
    average: Optional[float]    # None when there are no approved reviews
    count: int
