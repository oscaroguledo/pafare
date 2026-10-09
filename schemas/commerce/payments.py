from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from uuid import UUID
from datetime import datetime


class MethodBase(BaseModel):
    type: str = Field(..., description="Type of payment method (e.g., credit_card, paypal)")
    provider: Optional[str] = Field(None, description="Payment provider (e.g., Visa, PayPal)")
    last_four: Optional[str] = Field(None, max_length=4, description="Last four digits of the card number")
    expiry_month: Optional[int] = Field(None, ge=1, le=12, description="Card expiry month")
    expiry_year: Optional[int] = Field(None, ge=2000, description="Card expiry year")
    is_default: bool = Field(False, description="Whether this is the default payment method")


class MethodCreate(BaseModel):
    stripe_payment_method_id: str = Field(..., description="Stripe PaymentMethod id created client-side by Stripe.js")
    is_default: bool = Field(False, description="Whether to set this as the default payment method")
    payment_method_metadata: Optional[dict] = Field(None, description="Additional metadata (e.g. cardholder name)")


class MethodUpdate(BaseModel):
    is_default: Optional[bool] = None
    meta_data: Optional[dict] = None


class MethodResponse(MethodBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


# Payment Intent schemas
class IntentBase(BaseModel):
    amount: float
    currency: str = "USD"
    status: str = "pending"
    # This mirrors PaymentIntent.payment_method_id (models/commerce/payments.py), which stores
    # the raw Stripe payment-method id (e.g. "pm_xxx"), not our internal PaymentMethod.id UUID.
    payment_method_id: Optional[str] = None


class IntentUpdate(BaseModel):
    status: Optional[str] = None
    amount: Optional[float] = None


class IntentResponse(IntentBase):
    id: UUID
    user_id: UUID
    order_id: Optional[UUID] = None
    stripe_payment_intent_id: Optional[str] = None
    client_secret: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# Transaction schemas


# Refund schemas
class TxnBase(BaseModel):
    amount: float
    currency: str = "USD"
    transaction_type: str  # matches model field name
    status: str = "pending"
    description: Optional[str] = None


class TxnResponse(TxnBase):
    id: UUID
    user_id: UUID
    payment_intent_id: Optional[UUID] = None
    order_id: Optional[UUID] = None
    stripe_transaction_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    customer_name: Optional[str] = None
    payment_method: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class Refund(BaseModel):
    payment_intent_id: UUID
    amount: Optional[float] = None
    reason: str = "requested_by_customer"
