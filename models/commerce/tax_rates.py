from sqlalchemy import String, Numeric, Boolean, DateTime, func, Index
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
import uuid
from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7


class TaxRate(Base):
    """Tax rates by country and province/state"""
    __tablename__ = "tax_rates"
    __table_args__ = (
        Index('idx_tax_country_province', 'country_code', 'province_code'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    country_code: Mapped[str] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    country_name: Mapped[str] = mapped_column(String(100))
    province_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # State/Province code
    province_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 4))  # Tax rate as decimal (e.g., 0.13 for 13%)
    tax_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g., "GST", "VAT", "Sales Tax"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self):
        location = f"{self.country_code}"
        if self.province_code:
            location += f"-{self.province_code}"
        return f"<TaxRate {location}: {self.tax_rate * 100}%>"
