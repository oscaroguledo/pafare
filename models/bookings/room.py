from sqlalchemy import String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from core.db import GUID
from models.base import BaseModel
import uuid


class Room(BaseModel):
    """A physical room in a hotel."""
    __tablename__ = "rooms"
    __table_args__ = (
        # A room number is unique within its hotel; the leading hotel_id also serves "rooms of a hotel" lookups
        UniqueConstraint('hotel_id', 'number', name='uq_rooms_hotel_number'),
        {'schema': 'bookings'},
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.hotels.id", ondelete="CASCADE"))
    room_type_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("bookings.room_types.id", ondelete="RESTRICT"), index=True)
    number: Mapped[str] = mapped_column(String(20))

