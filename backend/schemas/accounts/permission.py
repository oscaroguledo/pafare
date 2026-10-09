import re

from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Optional
from datetime import datetime
from uuid import UUID


class PermissionCreate(BaseModel):
    """A scope named resource:action, e.g. 'rooms:edit'."""
    name: str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.match(r"^[a-z0-9_]+:[a-z0-9_]+$", value):
            raise ValueError("Permission name must look like 'resource:action', using letters, digits and underscores.")
        return value


class PermissionUpdate(BaseModel):
    """Only the description can change. The name is the scope code and token claims refer to it, so renaming
    means creating a new permission."""
    description: Optional[str] = None


class RolePermissionSet(BaseModel):
    """The full set of permissions a role should have."""
    permission_ids: List[UUID]


class PermissionResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
