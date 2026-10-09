"""Refund schemas for API requests and responses."""
from pydantic import BaseModel, Field, validator
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from models.commerce.refunds import RefundStatus, RefundReason, RefundType


class ItemRequest(BaseModel):
    """Request schema for individual refund items"""
    order_item_id: UUID = Field(..., description="ID of the order item to refund")
    quantity: int = Field(..., ge=1, description="Quantity to refund")
    condition_notes: Optional[str] = Field(None, max_length=500, description="Notes about item condition")


class Request(BaseModel):
    """Request schema for creating a refund"""
    reason: RefundReason = Field(..., description="Reason for refund")
    customer_reason: Optional[str] = Field(None, max_length=1000, description="Customer's detailed explanation")
    customer_notes: Optional[str] = Field(None, max_length=1000, description="Additional customer notes")
    items: List[ItemRequest] = Field(..., min_items=1, description="Items to refund")

    @validator('items')
    def validate_items(cls, v):
        if not v:
            raise ValueError("At least one item must be specified for refund")
        return v


class ItemResponse(BaseModel):
    """Response schema for refund items"""
    order_item_id: UUID
    name: str
    quantity: int
    amount: float
    condition_notes: Optional[str]


class TimelineItem(BaseModel):
    """Timeline item for refund progress"""
    status: str
    title: str
    description: str
    timestamp: Optional[datetime]
    completed: bool


class Response(BaseModel):
    """Response schema for refund details"""
    id: UUID
    order_id: UUID
    refund_number: str
    status: RefundStatus
    refund_type: RefundType
    reason: RefundReason
    requested_amount: float
    approved_amount: Optional[float]
    processed_amount: Optional[float]
    currency: str
    customer_reason: Optional[str]
    customer_notes: Optional[str]
    auto_approved: bool
    requires_return: bool
    return_shipping_paid: bool
    requested_at: datetime
    approved_at: Optional[datetime]
    processed_at: Optional[datetime]
    completed_at: Optional[datetime]
    items: List[ItemResponse]
    timeline: List[TimelineItem]
    customer: Optional[dict] = None  # staff only: {name, email}
    admin_notes: Optional[str] = None

    class Config:
        from_attributes = True


class ListResponse(BaseModel):
    """Response schema for refund list"""
    refunds: List[Response]
    total: int
    page: int
    limit: int


# Admin refund management schemas


class UpdateRefundStatus(BaseModel):
    status: str
    admin_notes: Optional[str] = None
