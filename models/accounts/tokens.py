"""Revoked JWT ids: checked on refresh and on every authenticated request."""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Index
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base, UTCDateTime


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"
    __table_args__ = (
        Index("idx_revoked_tokens_expires_at", "expires_at"),
        {"schema": "accounts"},
    )

    # Kept only until the token would have expired anyway; expired rows are purged on revoke.
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # Set when a refresh token is replaced by a new one: it keeps working until then, so two tabs refreshing
    # at the same moment don't sign each other out. Empty means revoked outright (sign-out).
    usable_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
