from pydantic import BaseModel, EmailStr, ConfigDict, model_validator, AwareDatetime
from typing import Optional
from datetime import datetime
from uuid import UUID

from models.accounts.user import UserRole, AccountStatus


class Base(BaseModel):
    email: EmailStr
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: UserRole = UserRole.CUSTOMER

    @model_validator(mode="after")
    def normalize_names(self):
        if not self.firstname and self.first_name:
            self.firstname = self.first_name
        if not self.lastname and self.last_name:
            self.lastname = self.last_name
        if not self.firstname:
            self.firstname = ""
        if not self.lastname:
            self.lastname = ""
        return self


class Create(Base):
    password: str
    phone: Optional[str] = None


class Update(BaseModel):
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    date_of_birth: Optional[AwareDatetime] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    # is_active is derived from account_status; admins change it via POST /{user_id}/activate/ or /deactivate/.


class UserRoleUpdate(BaseModel):
    role: UserRole


class Response(BaseModel):
    id: UUID
    email: EmailStr
    firstname: str
    lastname: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: UserRole
    account_status: AccountStatus
    verified: bool
    verified: bool
    is_active: bool
    date_of_birth: Optional[AwareDatetime] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None
        }
    )
