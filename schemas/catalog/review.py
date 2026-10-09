from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime
from uuid import UUID


class User(BaseModel):
    id: UUID
    firstname: Optional[str] = None
    lastname: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ProductRef(BaseModel):
    id: UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class Response(BaseModel):
    id: UUID
    rating: int
    comment: Optional[str]
    created_at: datetime
    user: Optional[User] = None
    product: Optional[ProductRef] = None

    model_config = ConfigDict(from_attributes=True)


class Base(BaseModel):
    product_id: UUID
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)


class Create(Base):
    pass


class Update(BaseModel):
    # product_id is deliberately absent: a review can't be moved to another product.
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)


class InDB(Base):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)