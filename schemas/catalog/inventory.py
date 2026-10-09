from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from uuid import UUID
from datetime import datetime

from models.catalog.inventories import InventoryStatus


# --- WarehouseLocation Schemas ---
class LocationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    address: Optional[str] = None
    description: Optional[str] = None


class LocationCreate(LocationBase):
    pass


class LocationUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None


class LocationResponse(LocationBase):
    id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )


# --- Inventory Schemas ---
class Base(BaseModel):
    variant_id: UUID
    location_id: UUID
    quantity: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=10, ge=0)


class Create(Base):
    pass


class Update(BaseModel):
    location_id: Optional[UUID] = None
    location_name: Optional[str] = None
    quantity: Optional[int] = Field(default=None, ge=0)
    low_stock_threshold: Optional[int] = Field(default=None, ge=0)


class Response(Base):
    # Auto-created inventory (e.g. from a new variant) has no location assigned yet.
    location_id: Optional[UUID] = None
    id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    quantity_available: Optional[int] = None
    reorder_point: Optional[int] = None
    inventory_status: Optional[InventoryStatus] = None
    last_restocked_at: Optional[datetime] = None
    last_sold_at: Optional[datetime] = None
    version: Optional[int] = None
    location: Optional[LocationResponse] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )


# --- StockAdjustment Schemas ---
class AdjustmentBase(BaseModel):
    inventory_id: UUID
    quantity_change: int
    reason: str = Field(..., min_length=1, max_length=255)
    adjusted_by_user_id: Optional[UUID] = None
    notes: Optional[str] = None


class AdjustmentCreate(BaseModel):
    variant_id: Optional[UUID] = None
    product_id: Optional[UUID] = None  # Accept product_id as alias
    location_id: Optional[UUID] = None
    quantity_change: int
    reason: str = Field(..., min_length=1, max_length=255)
    notes: Optional[str] = None
    adjustment_type: Optional[str] = None  # add/remove/set - informational


class AdjustmentResponse(AdjustmentBase):
    id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )