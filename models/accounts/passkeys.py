"""Passkeys (WebAuthn credentials): sign in with Face ID, Touch ID, Windows Hello or a phone."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, JSON, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7


class Passkey(Base):
    __tablename__ = "passkeys"
    __table_args__ = (
        Index("idx_passkeys_user_id", "user_id"),
        {"schema": "accounts"},
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id", ondelete="CASCADE"))
    # The authenticator's credential id (base64url) and public key; the private key never leaves the device.
    credential_id: Mapped[str] = mapped_column(String(512), unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    name: Mapped[str] = mapped_column(String(100))

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }
