"""Consolidated payment models: PaymentMethod, PaymentIntent, Transaction."""
from sqlalchemy import String, Boolean, ForeignKey, Numeric, Text, Integer, Date, DateTime, func, Index
from sqlalchemy.dialects.postgresql import JSON, ENUM as PG_ENUM
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from typing import Dict, Any, Optional
from enum import Enum
from datetime import datetime
import uuid

# Enums for Payment Method fields
class PaymentType(str, Enum):
    CARD = "card"
    BANK_ACCOUNT = "bank_account"
    MOBILE_MONEY = "mobile_money"
    OTHER = "other" # Generic for future expansion

class PaymentProvider(str, Enum):
    STRIPE = "stripe"
    PAYPAL = "paypal"
    MOMO = "momo" # Mobile Money provider (e.g., M-Pesa, MTN Mobile Money)
    GOOGLE_PAY = "google_pay"
    APPLE_PAY = "apple_pay"
    BANK_TRANSFER = "bank_transfer"
    UNKNOWN = "unknown" # For methods where provider isn't explicitly known

class CardBrand(str, Enum):
    VISA = "visa"
    VERVE = "verve"
    MASTERCARD = "mastercard"
    AMEX = "amex"
    DISCOVER = "discover"
    JCB = "jcb"
    DINERS_CLUB = "diners_club"
    UNIONPAY = "unionpay"
    UNKNOWN = "unknown"
    OTHER = "other" # For less common or newly introduced card brands


class PaymentIntentStatus(str, Enum):
    """Payment intent status types"""
    REQUIRES_PAYMENT_METHOD = "requires_payment_method"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    REQUIRES_CAPTURE = "requires_capture"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"


class TransactionType(str, Enum):
    """Transaction type types"""
    PAYMENT = "payment"
    REFUND = "refund"
    PAYOUT = "payout"
    CHARGEBACK = "chargeback"
    ADJUSTMENT = "adjustment"


class PaymentFailureReason(str, Enum):
    """Payment failure reason categories"""
    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_DECLINED = "card_declined"
    EXPIRED_CARD = "expired_card"
    INVALID_CARD = "invalid_card"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROCESSING_ERROR = "processing_error"
    NETWORK_ERROR = "network_error"
    FRAUD_SUSPECTED = "fraud_suspected"
    LIMIT_EXCEEDED = "limit_exceeded"
    UNKNOWN = "unknown"


class PaymentMethod(Base):
    """User payment methods - hard delete only"""
    __tablename__ = "payment_methods"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_payment_methods_user_id', 'user_id'),
        Index('idx_payment_methods_type', 'type'),
        Index('idx_payment_methods_provider', 'provider'),
        Index('idx_payment_methods_stripe_id', 'stripe_payment_method_id'),
        Index('idx_payment_methods_default', 'is_default'),
        Index('idx_payment_methods_active', 'is_active'),
        # Composite indexes for common queries
        Index('idx_payment_methods_user_active', 'user_id', 'is_active'),
        Index('idx_payment_methods_user_default', 'user_id', 'is_default'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id"))
    # Use PG_ENUM for type to map to PostgreSQL enum type
    type: Mapped[PaymentType] = mapped_column(PG_ENUM(PaymentType, name="payment_type"))
    provider: Mapped[PaymentProvider] = mapped_column(PG_ENUM(PaymentProvider, name="payment_provider"))  # stripe, paypal, momo
    last_four: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    expiry_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expiry_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brand: Mapped[Optional[CardBrand]] = mapped_column(PG_ENUM(CardBrand, name="card_brand"), nullable=True)  # visa, mastercard, etc.
    stripe_payment_method_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Only use JSONB for complex payment method data that needs querying
    payment_method_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # Store complex payment data

    # Relationships
    user = relationship("User", back_populates="payment_methods")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "type": self.type.value,
            "provider": self.provider.value,
            "last_four": self.last_four,
            "expiry_month": self.expiry_month,
            "expiry_year": self.expiry_year,
            "brand": self.brand.value if self.brand else None,
            "stripe_payment_method_id": self.stripe_payment_method_id,
            "is_default": self.is_default,
            "is_active": self.is_active,
            "payment_method_metadata": self.payment_method_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PaymentIntent(Base):
    """Payment intent tracking with hard delete only"""
    __tablename__ = "payment_intents"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_payment_intents_stripe_id', 'stripe_payment_intent_id'),
        Index('idx_payment_intents_user_id', 'user_id'),
        Index('idx_payment_intents_subscription_id', 'subscription_id'),
        Index('idx_payment_intents_order_id', 'order_id'),
        Index('idx_payment_intents_status', 'status'),
        Index('idx_payment_intents_currency', 'currency'),
        Index('idx_payment_intents_created_at', 'created_at'),
        Index('idx_payment_intents_expires_at', 'expires_at'),
        # Composite indexes for common queries
        Index('idx_payment_intents_user_status', 'user_id', 'status'),
        Index('idx_payment_intents_status_created', 'status', 'created_at'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Stripe payment intent ID
    stripe_payment_intent_id: Mapped[str] = mapped_column(String(255), unique=True)

    # User and subscription references
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id"))
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True)  # May be null for one-time payments
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("commerce.orders.id"), nullable=True)  # For order payments

    # Amount breakdown (JSONB for complex cost structure that may need querying)
    amount_breakdown: Mapped[dict] = mapped_column(JSON)

    # Currency
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Payment status
    status: Mapped[PaymentIntentStatus] = mapped_column(String(50), default=PaymentIntentStatus.REQUIRES_PAYMENT_METHOD)

    # Stripe verification details (JSONB for structured data)
    stripe_verification: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Payment method details
    payment_method_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payment_method_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "card", "bank_account", etc.

    # 3D Secure and SCA handling
    requires_action: Mapped[bool] = mapped_column(Boolean, default=False)
    client_secret: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Expiration
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Completion details
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Structured failure detail (Stripe error, retry_count), kept separate from
    # payment_intent_metadata so it doesn't clobber caller-supplied data there.
    failure_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Metadata for additional tracking (JSONB for structured payment data)
    payment_intent_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    user = relationship("User", back_populates="payment_intents")
    order = relationship("Order", back_populates="payment_intents")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert payment intent to dictionary"""
        return {
            "id": str(self.id),
            "stripe_payment_intent_id": self.stripe_payment_intent_id,
            "user_id": str(self.user_id),
            "subscription_id": str(self.subscription_id) if self.subscription_id else None,
            "order_id": str(self.order_id) if self.order_id else None,
            "amount_breakdown": self.amount_breakdown,
            "currency": self.currency,
            "status": self.status,
            "stripe_verification": self.stripe_verification,
            "payment_method_id": self.payment_method_id,
            "payment_method_type": self.payment_method_type,
            "requires_action": self.requires_action,
            "client_secret": self.client_secret,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "failed_at": self.failed_at.isoformat() if self.failed_at else None,
            "failure_reason": self.failure_reason,
            "metadata": self.payment_intent_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @property
    def amount(self) -> float:
        """Expose amount_breakdown's total as a top-level attribute for schemas expecting `amount`."""
        try:
            if isinstance(self.amount_breakdown, dict):
                return float(self.amount_breakdown.get("total", 0.0) or 0.0)
        except Exception:
            pass
        return 0.0


class Transaction(Base):
    """Financial transaction records - hard delete only"""
    __tablename__ = "transactions"
    __table_args__ = (
        # Indexes for search and performance
        Index('idx_transactions_user_id', 'user_id'),
        Index('idx_transactions_order_id', 'order_id'),
        Index('idx_transactions_payment_intent_id', 'payment_intent_id'),
        Index('idx_transactions_stripe_id', 'stripe_payment_intent_id'),
        Index('idx_transactions_status', 'status'),
        Index('idx_transactions_type', 'transaction_type'),
        Index('idx_transactions_currency', 'currency'),
        Index('idx_transactions_amount', 'amount'),
        Index('idx_transactions_idempotency_key', 'idempotency_key'),
        Index('idx_transactions_request_id', 'request_id'),
        Index('idx_transactions_created_at', 'created_at'),
        # Composite indexes for common queries
        Index('idx_transactions_user_status', 'user_id', 'status'),
        Index('idx_transactions_user_type', 'user_id', 'transaction_type'),
        Index('idx_transactions_status_created', 'status', 'created_at'),
        {'schema': 'commerce'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id"))
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("commerce.orders.id"), nullable=True)
    payment_intent_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), ForeignKey("commerce.payment_intents.id"), nullable=True)
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # pending, succeeded, failed, cancelled, refunded
    status: Mapped[str] = mapped_column(String(50))
    # payment, refund, payout, chargeback
    transaction_type: Mapped[TransactionType] = mapped_column(String(50))

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # GOLDEN RULE 2: Idempotency for payments
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # For tracking

    # Additional transaction metadata (Text for simple key-value storage)
    transaction_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Simple string metadata

    # Relationships
    user = relationship("User", back_populates="transactions")
    order = relationship("Order", back_populates="transactions")
    payment_intent = relationship("PaymentIntent")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "order_id": str(self.order_id) if self.order_id else None,
            "payment_intent_id": str(self.payment_intent_id) if self.payment_intent_id else None,
            "stripe_payment_intent_id": self.stripe_payment_intent_id,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "transaction_type": self.transaction_type,
            "description": self.description,
            "failure_reason": self.failure_reason,
            "idempotency_key": self.idempotency_key,
            "request_id": self.request_id,
            "metadata": self.transaction_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PaymentAnalytics(Base):
    """Daily payment analytics and metrics"""
    __tablename__ = "payment_analytics"

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Date for this analytics record
    date: Mapped[Date] = mapped_column(Date)

    # Payment volume metrics
    total_payments: Mapped[int] = mapped_column(Integer, default=0)
    successful_payments: Mapped[int] = mapped_column(Integer, default=0)
    failed_payments: Mapped[int] = mapped_column(Integer, default=0)
    pending_payments: Mapped[int] = mapped_column(Integer, default=0)

    # Success rate
    success_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.0)
    total_volume: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    successful_volume: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    average_payment_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)

    # Currency
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Breakdown by payment method (JSON: {"card": {...}, "bank_account": {...}})
    breakdown_by_method: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Breakdown by country (JSON: {"US": {...}, "CA": {...}})
    breakdown_by_country: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Breakdown by currency (JSON: {"USD": {...}, "EUR": {...}})
    breakdown_by_currency: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Failure analysis (JSON: {"insufficient_funds": 5, "card_declined": 3})
    failure_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Processing times (JSON: {"average_ms": 1500, "p95_ms": 3000})
    processing_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Additional metrics (JSON)
    additional_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        {'schema': 'commerce'},
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert payment analytics to dictionary"""
        return {
            "id": str(self.id),
            "date": self.date.isoformat() if self.date else None,
            "total_payments": self.total_payments,
            "successful_payments": self.successful_payments,
            "failed_payments": self.failed_payments,
            "pending_payments": self.pending_payments,
            "success_rate": self.success_rate,
            "total_volume": self.total_volume,
            "successful_volume": self.successful_volume,
            "average_payment_amount": self.average_payment_amount,
            "currency": self.currency,
            "breakdown_by_method": self.breakdown_by_method,
            "breakdown_by_country": self.breakdown_by_country,
            "breakdown_by_currency": self.breakdown_by_currency,
            "failure_breakdown": self.failure_breakdown,
            "processing_metrics": self.processing_metrics,
            "additional_metrics": self.additional_metrics,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }