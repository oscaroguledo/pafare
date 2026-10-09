from pydantic import BaseModel, Field, ConfigDict
from typing import List, Literal, Optional
from datetime import datetime
from uuid import UUID

from models.commerce.orders import OrderStatus, PaymentStatus, FulfillmentStatus, OrderSource


class Checkout(BaseModel):
    shipping_address_id: UUID
    shipping_method_id: UUID  # Reverted back to UUID since we're using database shipping methods
    payment_method_id: UUID
    notes: Optional[str] = None
    discount_code: Optional[str] = None
    frontend_calculated_total: Optional[float] = None  # For validation
    idempotency_key: Optional[str] = None  # For duplicate prevention
    source: Literal["web", "mobile"] = "web"  # which app placed the order


class Update(BaseModel):
    order_status: Optional[OrderStatus] = None
    payment_status: Optional[PaymentStatus] = None
    fulfillment_status: Optional[FulfillmentStatus] = None
    tracking_number: Optional[str] = None
    notes: Optional[str] = None


class ItemResponse(BaseModel):
    id: UUID
    variant_id: UUID
    quantity: int
    price_per_unit: float
    total_price: float
    variant: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class Response(BaseModel):
    id: UUID
    order_number: str
    user_id: UUID
    order_status: Optional[OrderStatus] = None
    payment_status: Optional[PaymentStatus] = None
    fulfillment_status: Optional[FulfillmentStatus] = None
    total_amount: float
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    tax_rate: Optional[float] = None
    shipping_cost: Optional[float] = None
    discount_amount: Optional[float] = None
    currency: str
    source: Optional[OrderSource] = None
    shipping_method: Optional[str] = None
    tracking_number: Optional[str] = None
    carrier: Optional[str] = None
    tracking_url: Optional[str] = None
    estimated_delivery: Optional[str] = None
    shipping_address: Optional[dict] = None
    billing_address: Optional[dict] = None
    customer_notes: Optional[str] = None
    internal_notes: Optional[str] = None
    items: List[ItemResponse]
    confirmed_at: Optional[datetime] = None
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Set when the bank asks the customer to verify (3-D Secure): the browser completes it with Stripe.js
    # and then calls POST /orders/{id}/complete-payment/.
    requires_action: bool = False
    client_secret: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )


# Order Intent schemas
class IntentBase(BaseModel):
    user_id: UUID
    total_amount: float
    currency: str = "USD"
    status: str = "pending"


class IntentCreate(IntentBase):
    pass


class IntentUpdate(BaseModel):
    status: Optional[str] = None
    total_amount: Optional[float] = None


class IntentResponse(IntentBase):
    id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# Order note schemas
class Note(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)


# Admin order management schemas
