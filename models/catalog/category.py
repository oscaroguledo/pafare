from sqlalchemy import String, Boolean, Integer, Text, func, Index
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, CHAR_LENGTH, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from datetime import datetime
from typing import Optional
import uuid


class Category(Base):
    """A store department; categories are flat, with no subcategories."""
    __tablename__ = "categories"
    __table_args__ = (
        Index('idx_categories_slug', 'slug'),
        {'schema': 'catalog'}
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    name: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    slug: Mapped[str] = mapped_column(String(150), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    products = relationship("Product", back_populates="category", lazy="select")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
