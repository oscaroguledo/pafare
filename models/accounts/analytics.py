"""User analytics models: UserSession and CustomerLifecycleMetrics."""
from sqlalchemy import String, ForeignKey, Numeric, Text, Integer, DateTime, func, Boolean, Enum as SQLEnum, Index
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from enum import Enum
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid


class TrafficSource(Enum):
    """Traffic source types"""
    DIRECT = "direct"
    ORGANIC_SEARCH = "organic_search"
    PAID_SEARCH = "paid_search"
    SOCIAL = "social"
    EMAIL = "email"
    REFERRAL = "referral"
    AFFILIATE = "affiliate"
    UNKNOWN = "unknown"


class UserSession(Base):
    """User session tracking for analytics"""
    __tablename__ = "user_sessions"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_user_sessions_session_id', 'session_id'),
        Index('idx_user_sessions_user_id', 'user_id'),
        Index('idx_user_sessions_created_at', 'created_at'),
        Index('idx_user_sessions_started_at', 'started_at'),
        Index('idx_user_sessions_ended_at', 'ended_at'),
        Index('idx_user_sessions_traffic_source', 'traffic_source'),
        Index('idx_user_sessions_converted', 'converted'),
        # Composite indexes for common queries
        Index('idx_user_sessions_user_created', 'user_id', 'created_at'),
        Index('idx_user_sessions_source_created', 'traffic_source', 'created_at'),
        {'schema': 'accounts'},
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Session identification
    session_id: Mapped[str] = mapped_column(String(255), unique=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("accounts.users.id"), nullable=True)

    # Session metadata
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    device_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    browser: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    os: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Traffic attribution
    traffic_source: Mapped[TrafficSource] = mapped_column(SQLEnum(TrafficSource), default=TrafficSource.DIRECT)
    referrer_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_content: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    utm_term: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Session timing
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Session metrics
    page_views: Mapped[int] = mapped_column(Integer, default=0)
    events_count: Mapped[int] = mapped_column(Integer, default=0)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)
    conversion_value: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Geographic data
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Relationships
    user = relationship("User", back_populates="sessions")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "session_id": self.session_id,
            "user_id": str(self.user_id) if self.user_id else None,
            "device_type": self.device_type,
            "browser": self.browser,
            "os": self.os,
            "traffic_source": self.traffic_source.value if self.traffic_source else None,
            "referrer_url": self.referrer_url,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "page_views": self.page_views,
            "events_count": self.events_count,
            "converted": self.converted,
            "conversion_value": self.conversion_value,
            "country": self.country,
            "region": self.region,
            "city": self.city,
        }


class CustomerLifecycleMetrics(Base):
    """Customer lifecycle and repeat purchase tracking"""
    __tablename__ = "customer_lifecycle_metrics"
    __table_args__ = (
        Index('idx_customer_lifecycle_user_id', 'user_id'),
        Index('idx_customer_lifecycle_created_at', 'created_at'),
        Index('idx_customer_lifecycle_segment', 'customer_segment'),
        {'schema': 'accounts'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Customer identification
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id"), unique=True)

    # Registration and first purchase
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime())
    first_purchase_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    time_to_first_purchase_hours: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Purchase behavior
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    total_revenue: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    average_order_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)

    # Timing metrics
    last_purchase_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    days_since_last_purchase: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    average_days_between_orders: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Refund metrics
    total_refunds: Mapped[int] = mapped_column(Integer, default=0)
    total_refund_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    refund_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.0)

    # Customer segmentation
    customer_segment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    lifetime_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    predicted_ltv: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Engagement metrics
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    total_page_views: Mapped[int] = mapped_column(Integer, default=0)
    average_session_duration: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)

    # Last updated
    metrics_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="lifecycle_metrics")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "first_purchase_at": self.first_purchase_at.isoformat() if self.first_purchase_at else None,
            "time_to_first_purchase_hours": self.time_to_first_purchase_hours,
            "total_orders": self.total_orders,
            "total_revenue": self.total_revenue,
            "average_order_value": self.average_order_value,
            "last_purchase_at": self.last_purchase_at.isoformat() if self.last_purchase_at else None,
            "days_since_last_purchase": self.days_since_last_purchase,
            "average_days_between_orders": self.average_days_between_orders,
            "total_refunds": self.total_refunds,
            "total_refund_amount": self.total_refund_amount,
            "refund_rate": self.refund_rate,
            "customer_segment": self.customer_segment,
            "lifetime_value": self.lifetime_value,
            "predicted_ltv": self.predicted_ltv,
            "total_sessions": self.total_sessions,
            "total_page_views": self.total_page_views,
            "average_session_duration": self.average_session_duration,
            "metrics_updated_at": self.metrics_updated_at.isoformat() if self.metrics_updated_at else None,
        }
