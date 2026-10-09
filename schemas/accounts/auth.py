from pydantic import BaseModel, EmailStr, field_validator, AwareDatetime
from typing import Optional

from schemas.accounts.user import strong_password


class Register(BaseModel):
    """Public sign-up. The role is always 'customer' and the account starts inactive until the email is verified."""
    email: EmailStr
    password: str
    firstname: str
    middlename: Optional[str] = ""
    lastname: str
    phone: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = "en"
    timezone: Optional[str] = "UTC"
    dob: Optional[AwareDatetime] = None
    gender: Optional[str] = None

    _password = field_validator("password")(strong_password)


class Refresh(BaseModel):
    refresh_token: str


class ForgotPassword(BaseModel):
    email: EmailStr


class ResetPassword(BaseModel):
    token: str
    new_password: str

    _password = field_validator("new_password")(strong_password)
