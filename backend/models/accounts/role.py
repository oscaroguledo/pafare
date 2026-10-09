from sqlalchemy import String, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, GUID
from models.base import BaseModel
from enum import Enum
import uuid
from typing import Optional


class Role_Permission(BaseModel):
    """Association table for many-to-many relationship between Role and Permission."""
    __tablename__ = "role_permissions"
    __table_args__ = (
        # One grant per (role, permission); its leading role_id also serves "permissions of a role" lookups
        UniqueConstraint('role_id', 'permission_id', name='uq_role_permissions_role_permission'),
        {'schema': 'accounts'},
    )

    role_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.roles.id", ondelete="CASCADE"))
    permission_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.permissions.id", ondelete="CASCADE"), index=True)

class Permission(BaseModel):
    """A single capability, named resource:action (e.g. 'orders:read')."""
    __tablename__ = "permissions"
    __table_args__ = {'schema': 'accounts'}

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class Role(BaseModel):
    """Role lookup table; users reference it through role_id."""
    __tablename__ = "roles"
    __table_args__ = {'schema': 'accounts'}

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

