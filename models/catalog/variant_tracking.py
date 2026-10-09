from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey, JSON, Boolean, func, Index, Enum as SQLEnum
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from enum import Enum
import uuid


class TrackingActionType(str, Enum):
    """Variant tracking action types"""
    ADDED = "added"
    REMOVED = "removed"
    PRICE_CHANGED = "price_changed"


class AnalyticsPeriodType(str, Enum):
    """Analytics period types"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class VariantTrackingEntry(Base):
    """Track when variants are added to subscriptions"""
    __tablename__ = "variant_tracking_entries"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_variant_tracking_entries_variant_id', 'variant_id'),
        Index('idx_variant_tracking_entries_subscription_id', 'subscription_id'),
        Index('idx_variant_tracking_entries_action_type', 'action_type'),
        Index('idx_variant_tracking_entries_timestamp', 'tracking_timestamp'),
        Index('idx_variant_tracking_entries_currency', 'currency'),
        # Composite indexes for common queries
        Index('idx_variant_tracking_entries_variant_action', 'variant_id', 'action_type'),
        Index('idx_variant_tracking_entries_sub_timestamp', 'subscription_id', 'tracking_timestamp'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Core tracking information
    variant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.product_variants.id"))
    subscription_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("commerce.subscriptions.id"))

    # Price tracking
    price_at_time: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Tracking metadata
    action_type: Mapped[TrackingActionType] = mapped_column(SQLEnum(TrackingActionType), default=TrackingActionType.ADDED)
    tracking_timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(timezone.utc))

    # Additional context
    entry_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    variant = relationship("ProductVariant", back_populates="tracking_entries")
    subscription = relationship("Subscription", back_populates="variant_tracking_entries")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert tracking entry to dictionary"""
        return {
            "id": str(self.id),
            "variant_id": str(self.variant_id),
            "subscription_id": str(self.subscription_id),
            "price_at_time": self.price_at_time,
            "currency": self.currency,
            "action_type": self.action_type,
            "tracking_timestamp": self.tracking_timestamp.isoformat() if self.tracking_timestamp else None,
            "metadata": self.entry_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VariantPriceHistory(Base):
    """Track price changes for variants over time"""
    __tablename__ = "variant_price_history"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_variant_price_history_variant_id', 'variant_id'),
        Index('idx_variant_price_history_changed_by', 'changed_by_user_id'),
        Index('idx_variant_price_history_effective_date', 'effective_date'),
        Index('idx_variant_price_history_change_reason', 'change_reason'),
        Index('idx_variant_price_history_currency', 'currency'),
        # Composite indexes for common queries
        Index('idx_variant_price_history_variant_effective', 'variant_id', 'effective_date'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Variant reference
    variant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.product_variants.id"))

    # Price information
    old_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    new_price: Mapped[float] = mapped_column(Numeric(10, 2))
    old_sale_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    new_sale_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Change metadata
    change_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    changed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("accounts.users.id"), nullable=True)
    effective_date: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(timezone.utc))

    # Impact tracking
    affected_subscriptions_count: Mapped[int] = mapped_column(Integer, default=0)

    # Additional context
    price_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    variant = relationship("ProductVariant", back_populates="price_history")
    changed_by = relationship("User", back_populates="variant_price_changes")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert price history to dictionary"""
        return {
            "id": str(self.id),
            "variant_id": str(self.variant_id),
            "old_price": self.old_price,
            "new_price": self.new_price,
            "old_sale_price": self.old_sale_price,
            "new_sale_price": self.new_sale_price,
            "currency": self.currency,
            "change_reason": self.change_reason,
            "changed_by_user_id": str(self.changed_by_user_id) if self.changed_by_user_id else None,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "affected_subscriptions_count": self.affected_subscriptions_count,
            "metadata": self.price_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VariantAnalytics(Base):
    """Aggregated analytics for product variants"""
    __tablename__ = "variant_analytics"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_variant_analytics_variant_id', 'variant_id'),
        Index('idx_variant_analytics_date', 'date'),
        Index('idx_variant_analytics_period_type', 'period_type'),
        Index('idx_variant_analytics_currency', 'currency'),
        Index('idx_variant_analytics_popularity_rank', 'popularity_rank'),
        Index('idx_variant_analytics_total_revenue', 'total_revenue'),
        # Composite indexes for common queries
        Index('idx_variant_analytics_variant_date', 'variant_id', 'date'),
        Index('idx_variant_analytics_date_period', 'date', 'period_type'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Variant reference
    variant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.product_variants.id"))

    # Time period for analytics
    date: Mapped[datetime] = mapped_column(UTCDateTime())
    period_type: Mapped[AnalyticsPeriodType] = mapped_column(SQLEnum(AnalyticsPeriodType), default=AnalyticsPeriodType.DAILY)

    # Subscription metrics
    total_subscriptions: Mapped[int] = mapped_column(Integer, default=0)
    new_subscriptions: Mapped[int] = mapped_column(Integer, default=0)
    canceled_subscriptions: Mapped[int] = mapped_column(Integer, default=0)
    active_subscriptions: Mapped[int] = mapped_column(Integer, default=0)

    # Revenue metrics
    total_revenue: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    average_subscription_duration_days: Mapped[int] = mapped_column(Integer, default=0)

    # Performance metrics
    churn_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.0)
    popularity_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Currency
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Additional metrics
    additional_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    variant = relationship("ProductVariant", back_populates="analytics")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert variant analytics to dictionary"""
        return {
            "id": str(self.id),
            "variant_id": str(self.variant_id),
            "date": self.date.isoformat() if self.date else None,
            "period_type": self.period_type,
            "total_subscriptions": self.total_subscriptions,
            "new_subscriptions": self.new_subscriptions,
            "canceled_subscriptions": self.canceled_subscriptions,
            "active_subscriptions": self.active_subscriptions,
            "total_revenue": self.total_revenue,
            "average_subscription_duration_days": self.average_subscription_duration_days,
            "churn_rate": self.churn_rate,
            "popularity_rank": self.popularity_rank,
            "currency": self.currency,
            "additional_metrics": self.additional_metrics,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VariantSubstitution(Base):
    """Track variant substitution suggestions and usage"""
    __tablename__ = "variant_substitutions"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_variant_substitutions_original_id', 'original_variant_id'),
        Index('idx_variant_substitutions_substitute_id', 'substitute_variant_id'),
        Index('idx_variant_substitutions_similarity_score', 'similarity_score'),
        Index('idx_variant_substitutions_reason', 'substitution_reason'),
        Index('idx_variant_substitutions_active', 'is_active'),
        Index('idx_variant_substitutions_acceptance_rate', 'acceptance_rate'),
        # Composite indexes for common queries
        Index('idx_variant_substitutions_original_active', 'original_variant_id', 'is_active'),
        Index('idx_variant_substitutions_substitute_active', 'substitute_variant_id', 'is_active'),
        {'schema': 'catalog'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Original and substitute variants
    original_variant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.product_variants.id"))
    substitute_variant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.product_variants.id"))

    # Substitution metadata
    similarity_score: Mapped[float] = mapped_column(Numeric(3, 2), default=0.0)  # 0.0 to 1.0
    substitution_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # "out_of_stock", "discontinued", "price_match"

    # Usage tracking
    times_suggested: Mapped[int] = mapped_column(Integer, default=0)
    times_accepted: Mapped[int] = mapped_column(Integer, default=0)
    acceptance_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.0)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Additional context
    substitution_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    original_variant = relationship("ProductVariant", foreign_keys=[original_variant_id], backref="substitution_suggestions")
    substitute_variant = relationship("ProductVariant", foreign_keys=[substitute_variant_id], backref="substitute_for")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert variant substitution to dictionary"""
        return {
            "id": str(self.id),
            "original_variant_id": str(self.original_variant_id),
            "substitute_variant_id": str(self.substitute_variant_id),
            "similarity_score": self.similarity_score,
            "substitution_reason": self.substitution_reason,
            "times_suggested": self.times_suggested,
            "times_accepted": self.times_accepted,
            "acceptance_rate": self.acceptance_rate,
            "is_active": self.is_active,
            "metadata": self.substitution_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }