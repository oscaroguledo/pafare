from pydantic import BaseModel, Field
from uuid import UUID
from pydantic import BaseModel, Field


class Add(BaseModel):
    variant_id: UUID = Field(..., description="Product variant ID")
    quantity: int = Field(default=1, ge=1, description="Quantity must be at least 1")


class UpdateItem(BaseModel):
    quantity: int
