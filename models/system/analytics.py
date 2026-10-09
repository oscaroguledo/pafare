"""System analytics models: AnalyticsEvent, ConversionFunnel, EventType."""
from sqlalchemy import String, ForeignKey, Numeric, Text, Integer, DateTime, func, Boolean, Enum as SQLEnum, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from enum import Enum
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid


class EventType(Enum):
    """Analytics event types"""
    PAGE_VIEW = "page_view"
    CART_ADD = "cart_add"
    CART_REMOVE = "cart_remove"
    CART_VIEW = "cart_view"
    CHECKOUT_START = "checkout_start"
    CHECKOUT_STEP = "checkout_step"
    CHECKOUT_COMPLETE = "checkout_complete"
    CHECKOUT_ABANDON = "checkout_abandon"
    PURCHASE = "purchase"
    REFUND_REQUEST = "refund_request"
    REFUND_COMPLETE = "refund_complete"
    USER_REGISTER = "user_register"
    USER_LOGIN = "user_login"


class AnalyticsEvent(Base):
    """Individual analytics events"""
    __tablename__ = "analytics_events"
    __table_args__ = (
        Index('idx_analytics_events_session_id', 'session_id'),
        Index('idx_analytics_events_user_id', 'user_id'),
        Index('idx_analytics_events_type', 'event_type'),
        Index('idx_analytics_events_created_at', 'created_at'),
        Index('idx_analytics_events_order_id', 'order_id'),
        # Composite indexes for analytics queries
        Index('idx_analytics_events_type_created', 'event_type', 'created_at'),
        Index('idx_analytics_events_user_type', 'user_id', 'event_type'),
        Index('idx_analytics_events_session_type', 'session_id', 'event_type'),
        {'schema': 'system'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Event identification
    session_id: Mapped[str] = mapped_column(String(255), ForeignKey("accounts.user_sessions.session_id"))
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("accounts.users.id"), nullable=True)
    event_type: Mapped[EventType] = mapped_column(SQLEnum(EventType))

    # Event data
    page_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    event_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # E-commerce specific fields
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("commerce.orders.id"), nullable=True)
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True)
    variant_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Financial data
    revenue: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timing
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User")
    order = relationship("Order")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "session_id": self.session_id,
            "user_id": str(self.user_id) if self.user_id else None,
            "event_type": self.event_type.value if self.event_type else None,
            "page_url": self.page_url,
            "page_title": self.page_title,
            "event_data": self.event_data,
            "order_id": str(self.order_id) if self.order_id else None,
            "product_id": str(self.product_id) if self.product_id else None,
            "variant_id": str(self.variant_id) if self.variant_id else None,
            "category": self.category,
            "revenue": self.revenue,
            "quantity": self.quantity,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class ConversionFunnel(Base):
    """Conversion funnel tracking"""
    __tablename__ = "conversion_funnels"
    __table_args__ = (
        Index('idx_conversion_funnels_session_id', 'session_id'),
        Index('idx_conversion_funnels_user_id', 'user_id'),
        Index('idx_conversion_funnels_created_at', 'created_at'),
        Index('idx_conversion_funnels_step', 'current_step'),
        {'schema': 'system'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Funnel identification
    session_id: Mapped[str] = mapped_column(String(255), ForeignKey("accounts.user_sessions.session_id"))
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("accounts.users.id"), nullable=True)

    # Funnel steps (0-based)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    max_step_reached: Mapped[int] = mapped_column(Integer, default=0)

    # Step timestamps
    landing_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    product_view_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    cart_add_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    checkout_start_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    purchase_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Funnel metadata
    abandoned_at_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    abandoned_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Financial data
    cart_value: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    purchase_value: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Relationships
    session = relationship("UserSession")
    user = relationship("User")
