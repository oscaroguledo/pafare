import re

from pydantic import BaseModel, EmailStr, ConfigDict, field_validator, model_validator, AwareDatetime
from typing import Optional
from datetime import datetime
from uuid import UUID

from models.accounts.user import AccountStatus


def strong_password(value: str) -> str:
    """At least 8 characters with an upper-case letter, a lower-case letter and a digit."""
    if len(value) < 8 or not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must be at least 8 characters with an upper-case letter, a lower-case letter and a digit")
    return value


class UserCreate(BaseModel):
    email: EmailStr
    firstname: Optional[str] = ""
    middlename: Optional[str] = ""
    lastname: Optional[str] = ""
    password: str
    account_status: AccountStatus = AccountStatus.ACTIVE
    phone: Optional[str] = None
    role_name: Optional[str] = None
    language: Optional[str] = "en"
    country: Optional[str] = None
    timezone: Optional[str] = "UTC"
    avatar_url: Optional[str] = None
    dob: Optional[AwareDatetime] = None
    gender: Optional[str] = None

    _password = field_validator("password")(strong_password)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserGet(BaseModel):
    """Look a user up by id, email, or both (either one matching is enough)."""
    id: Optional[UUID] = None
    email: Optional[EmailStr] = None

    @model_validator(mode="after")
    def require_one(self):
        if self.id is None and self.email is None:
            raise ValueError("Provide an id or an email")
        return self

class UserList(BaseModel):
    """Filters for the admin user list; every field is optional and they combine with AND."""
    search: Optional[str] = None          # matches first name, last name or email
    role_name: Optional[str] = None            # role name, e.g. "admin"
    status: Optional[AccountStatus] = None
    account_status: Optional[AccountStatus] = None
    firstname: Optional[str] = None
    middlename: Optional[str] = None
    lastname: Optional[str] = None
    phone: Optional[str] = None
    language: Optional[str] = None
    country: Optional[str] = None
    dob: Optional[AwareDatetime] = None
    gender: Optional[str] = None

class UserUpdate(BaseModel):
    """Fields that can be updated on a user; all optional."""
    firstname: Optional[str] = None
    middlename: Optional[str] = None
    lastname: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    avatar_url: Optional[str] = None
    dob: Optional[AwareDatetime] = None
    gender: Optional[str] = None

class UserResponse(BaseModel):
    """A user as returned by the API. Every field is required: nullable ones (Optional) must still be
    present, and are None when the user hasn't set them."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    firstname: str
    middlename: Optional[str]
    lastname: str
    role_id: UUID
    role_name: str
    account_status: AccountStatus
    phone: Optional[str]
    country: Optional[str]
    language: str
    timezone: Optional[str]
    avatar_url: Optional[str]
    dob: Optional[AwareDatetime]
    gender: Optional[str]
    last_login: Optional[AwareDatetime]
    created_at: AwareDatetime
    updated_at: Optional[AwareDatetime]



class RoleChange(BaseModel):
    role_id: UUID


class EmailChange(BaseModel):
    new_email: EmailStr
