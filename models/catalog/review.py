from sqlalchemy import Boolean, ForeignKey, Text, Integer, DateTime, func, Index
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from datetime import datetime
from typing import Optional
import uuid


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_reviews_product_id', 'product_id'),
        Index('idx_reviews_user_id', 'user_id'),
        Index('idx_reviews_rating', 'rating'),
        Index('idx_reviews_verified', 'is_verified_purchase'),
        Index('idx_reviews_approved', 'is_approved'),
        Index('idx_reviews_created_at', 'created_at'),
        # Composite indexes for common queries
        Index('idx_reviews_product_approved', 'product_id', 'is_approved'),
        Index('idx_reviews_product_rating', 'product_id', 'rating'),
        Index('idx_reviews_user_approved', 'user_id', 'is_approved'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    product_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.products.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id"))
    rating: Mapped[int] = mapped_column(Integer)  # 1-5 stars
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_verified_purchase: Mapped[bool] = mapped_column(Boolean, default=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    product = relationship("Product", back_populates="reviews")
    user = relationship("User", back_populates="reviews")
