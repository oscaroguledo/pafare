"""Order models (hard delete only): Order, OrderItem, TrackingEvent."""
from sqlalchemy import String, ForeignKey, Numeric, Text, Integer, DateTime, func, Enum as SQLEnum, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, CHAR_LENGTH, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from enum import Enum
from datetime import datetime
from typing import Optional
import uuid


class OrderStatus(str, Enum):
    """Order status types"""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, Enum):
    """Payment status types"""
    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class FulfillmentStatus(str, Enum):
    """Fulfillment status types"""
    UNFULFILLED = "unfulfilled"
    PARTIAL = "partial"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class OrderSource(str, Enum):
    """Order source types"""
    WEB = "web"
    MOBILE = "mobile"
    API = "api"
    ADMIN = "admin"


class Order(Base):
    """Simplified order model with essential pricing fields only"""
    __tablename__ = "orders"
    __table_args__ = (
        # Optimized indexes for common order queries
        Index('idx_orders_user_status', 'user_id', 'order_status'),
        Index('idx_orders_subscription_id', 'subscription_id'),
        Index('idx_orders_payment_status', 'payment_status', 'created_at'),
        Index('idx_orders_created_at', 'created_at'),
        Index('idx_orders_fulfillment_status', 'fulfillment_status'),
        Index('idx_orders_order_number', 'order_number'),
        Index('idx_orders_total_currency', 'total_amount', 'currency'),
        Index('idx_orders_tracking', 'tracking_number'),
        Index('idx_orders_confirmed_shipped', 'confirmed_at', 'shipped_at'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Order identification
    order_number: Mapped[str] = mapped_column(String(50), unique=True)

    # Customer reference
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id"))

    # Subscription reference for recurring orders
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("commerce.subscriptions.id"), nullable=True)

    # Status fields as columns for fast querying and indexing
    order_status: Mapped[OrderStatus] = mapped_column(SQLEnum(OrderStatus), default=OrderStatus.PENDING)
    payment_status: Mapped[PaymentStatus] = mapped_column(SQLEnum(PaymentStatus), default=PaymentStatus.PENDING)
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(SQLEnum(FulfillmentStatus), default=FulfillmentStatus.UNFULFILLED)

    # Simplified financial information - only the essentials
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2))  # Sum of all product variant prices
    shipping_cost: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)  # Shipping cost (renamed from shipping_amount)
    discount_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)  # Discount amount applied to order
    promocode_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("commerce.promocodes.id", ondelete="SET NULL"), nullable=True)
    tax_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)  # Tax amount
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.0)  # Tax rate applied (e.g., 0.08 for 8%)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))  # Final total
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Shipping information as columns for frequent access
    shipping_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tracking_number: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    carrier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Use JSONB only for complex address data that benefits from querying
    billing_address: Mapped[dict] = mapped_column(JSON)
    shipping_address: Mapped[dict] = mapped_column(JSON)

    # Important lifecycle dates as columns
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Notes as text (simple storage)
    customer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Failure tracking
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Idempotency and source tracking
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    source: Mapped[OrderSource] = mapped_column(SQLEnum(OrderSource), default=OrderSource.WEB)

    # Relationships with optimized lazy loading
    user = relationship("User", back_populates="orders")
    subscription = relationship("Subscription", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan", lazy="selectin")
    tracking_events = relationship("TrackingEvent", back_populates="order", cascade="all, delete-orphan", lazy="select")
    transactions = relationship("Transaction", back_populates="order", lazy="select")
    payment_intents = relationship("PaymentIntent", back_populates="order", lazy="select")
    refunds = relationship("Refund", back_populates="order", lazy="select")
    shipments = relationship("ShipmentTracking", back_populates="order", cascade="all, delete-orphan", lazy="select")

    def to_dict(self) -> dict:
        """Convert order to dictionary for API responses"""
        return {
            "id": str(self.id),
            "order_number": self.order_number,
            "user_id": str(self.user_id),
            "order_status": self.order_status,
            "payment_status": self.payment_status,
            "fulfillment_status": self.fulfillment_status,
            "subtotal": self.subtotal,
            "shipping_cost": self.shipping_cost,
            "discount_amount": self.discount_amount,
            "tax_amount": self.tax_amount,
            "tax_rate": self.tax_rate,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "shipping_method": self.shipping_method,
            "tracking_number": self.tracking_number,
            "carrier": self.carrier,
            "billing_address": self.billing_address,
            "shipping_address": self.shipping_address,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "shipped_at": self.shipped_at.isoformat() if self.shipped_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "customer_notes": self.customer_notes,
            "internal_notes": self.internal_notes,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class OrderItem(Base):
    """Order items - hard delete with orders"""
    __tablename__ = "order_items"
    __table_args__ = (
        Index('idx_order_items_order_id', 'order_id'),
        Index('idx_order_items_variant_id', 'variant_id'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    order_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("commerce.orders.id"))
    variant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("catalog.product_variants.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    price_per_unit: Mapped[float] = mapped_column(Numeric(10, 2))
    total_price: Mapped[float] = mapped_column(Numeric(10, 2))

    # Relationships
    order = relationship("Order", back_populates="items")
    variant = relationship("ProductVariant", back_populates="order_items")
    shipment = relationship("ShipmentTracking", back_populates="order_item", lazy="select")

    def to_dict(self) -> dict:
        """Convert order item to dictionary"""
        return {
            "id": str(self.id),
            "order_id": str(self.order_id),
            "variant_id": str(self.variant_id),
            "quantity": self.quantity,
            "price_per_unit": self.price_per_unit,
            "total_price": self.total_price,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TrackingEvent(Base):
    """Order tracking events - hard delete with orders"""
    __tablename__ = "tracking_events"
    __table_args__ = (
        Index('idx_tracking_events_order_id', 'order_id'),
        Index('idx_tracking_events_created_at', 'created_at'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    order_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("commerce.orders.id"))
    status: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    order = relationship("Order", back_populates="tracking_events")

    def to_dict(self) -> dict:
        """Convert tracking event to dictionary"""
        return {
            "id": str(self.id),
            "order_id": str(self.order_id),
            "status": self.status,
            "description": self.description,
            "location": self.location,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }