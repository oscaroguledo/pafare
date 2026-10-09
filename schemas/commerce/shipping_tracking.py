"""Shipping tracking schemas."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from models.commerce.shipping_tracking import TrackingStatus, ShipmentType


class Create(BaseModel):
    order_id: str = Field(..., description="Order ID")
    order_item_id: Optional[str] = Field(None, description="Order item ID for multi-item shipments")
    carrier: str = Field(..., description="Shipping carrier code (e.g. 'ups')")
    tracking_number: str = Field(..., description="Tracking number")
    shipment_type: ShipmentType = Field(ShipmentType.STANDARD, description="Shipment type")
    origin_address: Optional[dict] = Field(None, description="Origin address")
    destination_address: Optional[dict] = Field(None, description="Destination address")
    delivery_instructions: Optional[str] = Field(None, description="Delivery instructions")
    package_weight: Optional[float] = Field(None, description="Package weight in kg")
    package_dimensions: Optional[dict] = Field(None, description="Package dimensions")
    package_value: Optional[float] = Field(None, description="Package value")
    insurance_amount: Optional[float] = Field(None, description="Insurance amount")
    service_level: Optional[str] = Field(None, description="Service level")
    delivery_signature_required: bool = Field(False, description="Signature required")
    delivery_confirmation: Optional[str] = Field(None, description="Delivery confirmation")
    notes: Optional[str] = Field(None, description="Notes")
    internal_notes: Optional[str] = Field(None, description="Internal notes")
    shipped_at: Optional[datetime] = Field(None, description="Shipped at timestamp")


class Update(BaseModel):
    status: TrackingStatus = Field(..., description="New tracking status")
    event_description: Optional[str] = Field(None, description="Event description")
    event_location: Optional[dict] = Field(None, description="Event location")
    contact_name: Optional[str] = Field(None, description="Contact name")
    contact_phone: Optional[str] = Field(None, description="Contact phone")


class Track(BaseModel):
    tracking_number: str = Field(..., description="Tracking number")
    carrier: str = Field(..., description="Shipping carrier code (e.g. 'ups')")
