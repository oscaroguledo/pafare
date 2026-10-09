from sqlalchemy import String, Boolean, DateTime, func, Index
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from datetime import datetime
from typing import Dict, Any, Optional
import uuid


class Carrier(Base):
    """A shipping carrier (UPS, FedEx, DHL, ...), configurable without a code change."""
    __tablename__ = "carriers"
    __table_args__ = (
        Index('idx_carriers_code', 'code'),
        Index('idx_carriers_active', 'is_active'),
        {'schema': 'commerce'}
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    tracking_url_template: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    providers = relationship("ShippingProvider", back_populates="carrier")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "code": self.code,
            "name": self.name,
            "tracking_url_template": self.tracking_url_template,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
