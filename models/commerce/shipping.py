from sqlalchemy import String, Boolean, DateTime, func, Numeric, Text, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from core.db import Base, CHAR_LENGTH, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from datetime import datetime
from typing import Optional
import uuid


class ShippingMethod(Base):
    __tablename__ = "shipping_methods"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_shipping_methods_name', 'name'),
        Index('idx_shipping_methods_active', 'is_active'),
        Index('idx_shipping_methods_price', 'price'),
        Index('idx_shipping_methods_estimated_days', 'estimated_days'),
        # Composite indexes for common queries
        Index('idx_shipping_methods_active_price', 'is_active', 'price'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    name: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    estimated_days: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Simple metadata
    carrier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tracking_url_template: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
