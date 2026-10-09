from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Literal, Optional
from datetime import datetime


class Base(BaseModel):
    code: str = Field(..., min_length=1)
    description: Optional[str] = None
    discount_type: Literal["percentage", "fixed"]
    value: float = Field(..., gt=0)
    minimum_order_amount: Optional[float] = None
    maximum_discount_amount: Optional[float] = None
    usage_limit: Optional[int] = None
    is_active: bool = True
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class Create(Base):
    pass


class Update(BaseModel):
    code: Optional[str] = None
    description: Optional[str] = None
    discount_type: Optional[Literal["percentage", "fixed"]] = None
    value: Optional[float] = None
    minimum_order_amount: Optional[float] = None
    maximum_discount_amount: Optional[float] = None
    usage_limit: Optional[int] = None
    is_active: Optional[bool] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    @field_validator("code")
    @classmethod
    def _upper(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().upper() if v else v


class InDB(Base):
    id: str
    used_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Validate promocode schemas
class ValidateRequest(BaseModel):
    code: str
    subtotal: Optional[float] = Field(None, ge=0)
