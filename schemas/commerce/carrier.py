from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime
from uuid import UUID


class Create(BaseModel):
    code: str
    name: str
    tracking_url_template: Optional[str] = None
    is_active: bool = True


class Update(BaseModel):
    name: Optional[str] = None
    tracking_url_template: Optional[str] = None
    is_active: Optional[bool] = None


class Response(BaseModel):
    id: UUID
    code: str
    name: str
    tracking_url_template: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: lambda v: v.isoformat() if v else None}
    )


class ListResponse(BaseModel):
    carriers: List[Response]
    total: int
