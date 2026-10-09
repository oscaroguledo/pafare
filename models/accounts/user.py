from sqlalchemy import String, Boolean, ForeignKey, DateTime, Integer, func, Index, JSON
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import CHAR_LENGTH, GUID, UTCDateTime
from models.base import BaseModel
from core.utils.uuid_utils import uuid7
from enum import Enum
import uuid
from datetime import datetime
from typing import Optional

class Gender(str, Enum):
    MALE = "male"
    FEMALE ="female"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class User(BaseModel):
    """User model with hard delete only"""
    __tablename__ = "users"
    __table_args__ = {'schema': 'accounts'}

    firstname: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    middlename: Mapped[Optional[str]] = mapped_column(String(CHAR_LENGTH), nullable=True)
    lastname: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    email: Mapped[str] = mapped_column(String(CHAR_LENGTH), unique=True, index=True)
    passwordhash: Mapped[str] = mapped_column(String(CHAR_LENGTH))

    account_status: Mapped[AccountStatus] = mapped_column(String(50), default=AccountStatus.ACTIVE, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.roles.id"), index=True)

    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(10), default="en", index=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    dob: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True, index=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=True, index=True)


    @property
    def full_name(self) -> str:
        """Get user's full name"""
        return " ".join(p for p in (self.firstname, self.middlename, self.lastname) if p)

    @property
    def is_active(self) -> bool:
        """True when account_status is 'active'."""
        return self.account_status == AccountStatus.ACTIVE
