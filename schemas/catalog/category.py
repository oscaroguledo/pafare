from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime
from uuid import UUID


class CategoryBrief(BaseModel):
    """Minimal category info embedded in product responses."""
    id: UUID
    name: str
    slug: str

    model_config = ConfigDict(from_attributes=True)


class Create(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class Update(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class Response(BaseModel):
    id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: lambda v: v.isoformat() if v else None}
    )


class ListResponse(BaseModel):
    categories: List[Response]
    total: int
