"""The store owner's account, kept as SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD say on every start."""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.logging import get_structured_logger
from core.utils.encryption import PasswordManager
from core.utils.uuid_utils import uuid7
from models.accounts.user import AccountStatus, User, UserRole, VerificationStatus
from schemas.accounts.auth import strong_password

logger = get_structured_logger(__name__)

# Production needs a longer password than customers do, and never the one from the example env files.
PRODUCTION_MIN_LENGTH = 16
PLACEHOLDERS = {"change-me", "changeme", "your_superadmin_password", "password", "password123"}


def check_password(password: str, production: bool) -> None:
    """Raise ValueError when the password is too weak to guard the owner's account."""
    strong_password(password)
    if production and (len(password) < PRODUCTION_MIN_LENGTH or password.lower() in PLACEHOLDERS):
        raise ValueError(f"SUPERADMIN_PASSWORD must be at least {PRODUCTION_MIN_LENGTH} characters and not a placeholder in production")


async def ensure_superadmin(db: AsyncSession) -> Optional[User]:
    """Create or refresh the owner's account: a verified, active superadmin whose password is the configured one.
    Does nothing when either setting is empty."""
    email, password = settings.SUPERADMIN_EMAIL.strip().lower(), settings.SUPERADMIN_PASSWORD
    if not email or not password:
        return None
    check_password(password, settings.ENVIRONMENT.lower() == "production")

    user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if user is None:
        user = User(id=uuid7(), email=email, firstname="Store", lastname="Owner",
                    hashed_password=PasswordManager.hash_password(password))
        db.add(user)
    elif not user.hashed_password or not PasswordManager.verify_password(password, user.hashed_password):
        user.hashed_password = PasswordManager.hash_password(password)
    user.role = UserRole.SUPERADMIN.value
    user.account_status = AccountStatus.ACTIVE.value
    user.verification_status = VerificationStatus.VERIFIED.value
    user.verification_token = user.token_expiration = None
    user.failed_login_attempts, user.locked_until = 0, None
    await db.commit()
    logger.info(f"Superadmin account {email} is ready")
    return user
