import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7

__all__ = ["Base", "UUIDPrimaryKeyMixin", "TimestampMixin", "BaseModel"]


class UUIDPrimaryKeyMixin:
    """Time-ordered UUIDv7 primary key."""
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)


class TimestampMixin:
    """created_at / updated_at, both indexed for sorting and range filters."""
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False, index=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=True, index=True)


class BaseModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Abstract base: id, created_at, updated_at."""
    __abstract__ = True
