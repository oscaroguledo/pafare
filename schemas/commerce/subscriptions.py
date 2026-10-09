"""Subscription schemas"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

from models.commerce.subscriptions import SubscriptionStatus, BillingCycle


class Create(BaseModel):
    """Create subscription"""
    name: Optional[str] = "My Subscription"
    variant_ids: Optional[List[str]] = []
    variant_quantities: Optional[Dict[str, int]] = {}
    delivery_address_id: Optional[UUID] = None
    shipping_method_id: Optional[UUID] = None
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    discount_code: Optional[str] = None
    current_period_start: Optional[str] = None


class Update(BaseModel):
    """Update subscription"""
    name: Optional[str] = None
    delivery_address_id: Optional[UUID] = None
    shipping_method_id: Optional[UUID] = None
    auto_renew: Optional[bool] = None
    variant_ids: Optional[List[str]] = None
    variant_quantities: Optional[Dict[str, int]] = None
    current_period_start: Optional[str] = None


class CostCalculation(BaseModel):
    """Request to calculate subscription cost"""
    variant_ids: List[str]
    variant_quantities: Optional[Dict[str, int]] = {}
    delivery_address_id: Optional[UUID] = None
    shipping_method_id: Optional[UUID] = None
    discount_code: Optional[str] = None


class AddProducts(BaseModel):
    """Add products to subscription"""
    variant_ids: List[str]
    variant_quantities: Optional[Dict[str, int]] = {}


class RemoveProducts(BaseModel):
    """Remove products from subscription"""
    variant_ids: List[str]


class UpdateQuantity(BaseModel):
    """Update variant quantity in subscription"""
    variant_id: str
    quantity: int = Field(gt=0, description="New quantity (must be greater than 0)")


class DiscountApplication(BaseModel):
    """Apply discount to subscription"""
    discount_code: str


class ChangeFrequency(BaseModel):
    """Change subscription billing frequency"""
    frequency: BillingCycle


class SkipShipment(BaseModel):
    """Skip the upcoming shipment, optionally to a specific date"""
    next_shipment_date: Optional[str] = None


class Response(BaseModel):
    """Subscription response"""
    id: str
    user_id: str
    name: str
    status: SubscriptionStatus
    currency: str
    billing_cycle: BillingCycle
    auto_renew: bool
    next_billing_date: Optional[datetime]
    delivery_address_id: Optional[str]
    shipping_method_id: Optional[str]

    # At-creation prices
    price_at_creation: Optional[float]
    variant_prices_at_creation: Optional[List[Dict[str, Any]]]
    shipping_amount_at_creation: Optional[float]
    tax_amount_at_creation: Optional[float]
    
    # Current prices
    current_variant_prices: Optional[List[Dict[str, Any]]]
    current_shipping_amount: Optional[float]
    current_tax_amount: Optional[float]
    
    # Discount
    discount: Optional[Dict[str, Any]]
    
    # Products
    products: Optional[List[Dict[str, Any]]] = []
    
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )
