from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Optional
from datetime import date, datetime
from uuid import UUID


class RoomCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    hotel_id: UUID
    room_type_id: UUID
    number: str = Field(min_length=1, max_length=20)


class RoomUpdate(BaseModel):
    """Fields that can change; all optional. A room never moves to another hotel."""
    model_config = ConfigDict(str_strip_whitespace=True)

    room_type_id: Optional[UUID] = None
    number: Optional[str] = Field(None, min_length=1, max_length=20)


class RoomList(BaseModel):
    """Filters for the room list. Give `available_from` and `available_to` together to get only rooms
    with no active booking overlapping those nights."""
    hotel_id: Optional[UUID] = None
    room_type_id: Optional[UUID] = None
    search: Optional[str] = None            # matches the room number
    available_from: Optional[date] = None   # first night
    available_to: Optional[date] = None     # check-out day

    @model_validator(mode="after")
    def both_or_neither(self):
        if (self.available_from is None) != (self.available_to is None):
            raise ValueError("available_from and available_to must be given together")
        if self.available_from and self.available_to <= self.available_from:
            raise ValueError("available_to must be after available_from")
        return self


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hotel_id: UUID
    hotel_name: str
    room_type_id: UUID
    room_type_name: str
    number: str
    created_at: datetime
    updated_at: Optional[datetime]
