import re

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID

class RoleBase(BaseModel):

    description: Optional[str] = None

    @field_validator("name", check_fields=False)  # `name` is declared on the subclasses
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not re.match(r"^[a-zA-Z0-9_]+$", value):
            raise ValueError("Role name must contain only alphanumeric characters and underscores.")
        return value.lower()
class RoleCreate(RoleBase):
    name: str
    
class RoleUpdate(RoleBase):
    name: Optional[str] = None

    
class RoleResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)