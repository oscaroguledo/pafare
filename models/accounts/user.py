from sqlalchemy import String, Boolean, ForeignKey, DateTime, Integer, func, Index, JSON
from sqlalchemy.orm import relationship, Mapped, mapped_column
from core.db import Base, CHAR_LENGTH, GUID, UTCDateTime
from core.utils.uuid_utils import uuid7
from enum import Enum
import uuid
from datetime import datetime
from typing import Optional

class UserRole(str, Enum):
    GUEST = "guest"
    SUPERADMIN = "superadmin"  # the store owner: the only one who adds or removes admins
    ADMIN = "admin"
    MANAGER = "manager"
    SUPPORT = "support"
    CUSTOMER = "customer"


# Role hierarchy: staff manage roles/accounts below theirs; superadmin via CLI only
ROLE_RANK = {
    UserRole.GUEST: 0, UserRole.CUSTOMER: 0, UserRole.SUPPORT: 1,
    UserRole.MANAGER: 2, UserRole.ADMIN: 3, UserRole.SUPERADMIN: 4,
}
# Roles that open the admin dashboard and the admin API.
STAFF_ROLES = (UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.MANAGER)


def role_rank(role) -> int:
    """Rank of a role given as a UserRole or its stored string; unknown roles rank lowest."""
    try:
        return ROLE_RANK[UserRole(role.value if isinstance(role, Enum) else str(role).lower())]
    except ValueError:
        return 0


def outranks(actor_role, other_role) -> bool:
    """True when actor_role is strictly above other_role."""
    return role_rank(actor_role) > role_rank(other_role)

class Gender(str, Enum):
    MALE = "male"
    FEMALE ="female"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    PENDING = "pending"


class User(Base):
    """Optimized User model with hard delete only"""
    __tablename__ = "users"
    __table_args__ = (
        # Optimized indexes for common queries
        Index('idx_users_email_account_status', 'email', 'account_status'),
        Index('idx_users_role_verification_status', 'role', 'verification_status'),
        Index('idx_users_country_language', 'country', 'language'),
        Index('idx_users_last_login', 'last_login'),
        Index('idx_users_stripe_customer', 'stripe_customer_id'),
        Index('idx_users_date_of_birth', 'date_of_birth'),
        Index('idx_users_gender', 'gender'),
        {'schema': 'accounts'}
    )

    # Common fields (previously from BaseModel)
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    # Core identity fields - frequently queried
    email: Mapped[str] = mapped_column(String(CHAR_LENGTH), unique=True)
    firstname: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    lastname: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    hashed_password: Mapped[str] = mapped_column(String(CHAR_LENGTH))

    # Status fields as columns for fast filtering
    role: Mapped[UserRole] = mapped_column(String(50), default=UserRole.CUSTOMER)
    account_status: Mapped[AccountStatus] = mapped_column(String(50), default=AccountStatus.ACTIVE)
    verification_status: Mapped[VerificationStatus] = mapped_column(String(50), default=VerificationStatus.UNVERIFIED)

    # Contact information
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Profile information - frequently accessed
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    timezone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Optional profile fields
    date_of_birth: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Activity tracking
    last_login: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    # Core identity fields
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # External integrations
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(CHAR_LENGTH), nullable=True, unique=True)

    # Use JSON for complex user preferences that need querying (cross-platform compatible)
    preferences: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # User settings, notification prefs

    # Simple fields as text for better performance
    verification_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    token_expiration: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Password reset fields
    reset_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reset_token_expires: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Relationships with optimized lazy loading
    addresses = relationship("Address", back_populates="user", cascade="all, delete-orphan", lazy="selectin")
    cart = relationship("Cart", back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="select")
    orders = relationship("Order", back_populates="user", lazy="select")  # Don't eager load orders
    reviews = relationship("Review", back_populates="user", lazy="select")
    subscriptions = relationship("Subscription", back_populates="user", lazy="select")
    payment_methods = relationship("PaymentMethod", back_populates="user", lazy="select")
    transactions = relationship("Transaction", back_populates="user", lazy="select")
    payment_intents = relationship("PaymentIntent", back_populates="user", lazy="select")
    sessions = relationship("UserSession", back_populates="user", lazy="select")
    lifecycle_metrics = relationship("CustomerLifecycleMetrics", back_populates="user", lazy="select")
    
    # Refund relationships - using string references to avoid circular imports
    created_refunds = relationship("Refund", foreign_keys="[Refund.user_id]", back_populates="user", lazy="select")
    reviewed_refunds = relationship("Refund", foreign_keys="[Refund.reviewed_by]", back_populates="reviewer", lazy="select")
    processed_refunds = relationship("Refund", foreign_keys="[Refund.processed_by]", back_populates="processor", lazy="select")
    
    # Inventory and tracking relationships
    stock_adjustments = relationship("StockAdjustment", back_populates="adjusted_by", lazy="select")
    variant_price_changes = relationship("VariantPriceHistory", back_populates="changed_by", lazy="select")

    @property
    def is_staff(self) -> bool:
        """True for superadmins, admins and managers: the people who use the admin dashboard."""
        role = self.role.value if isinstance(self.role, Enum) else str(self.role or "").lower()
        return role in {r.value for r in STAFF_ROLES}

    @property
    def full_name(self) -> str:
        """Get user's full name"""
        return f"{self.firstname} {self.lastname}"

    @property
    def verified(self) -> bool:
        """True when verification_status is 'verified'."""
        return self.verification_status == VerificationStatus.VERIFIED

    @property
    def is_active(self) -> bool:
        """True when account_status is 'active'."""
        return self.account_status == AccountStatus.ACTIVE

    @property
    def default_address(self):
        """Get user's default address"""
        return next((addr for addr in self.addresses if addr.is_default), None)


    


    def to_dict(self) -> dict:
        """Convert user to dictionary for API responses"""
        return {
            "id": str(self.id),
            "email": self.email,
            "firstname": self.firstname,
            "lastname": self.lastname,
            "full_name": self.full_name,
            "role": self.role.value if isinstance(self.role, Enum) else self.role,
            "account_status": self.account_status.value if isinstance(self.account_status, Enum) else self.account_status,
            "verification_status": self.verification_status.value if isinstance(self.verification_status, Enum) else self.verification_status,
            "phone": self.phone,
            "phone_verified": self.phone_verified,
            "avatar_url": self.avatar_url,
            "country": self.country,
            "language": self.language,
            "timezone": self.timezone,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "gender": self.gender,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "failed_login_attempts": self.failed_login_attempts,
            "locked_until": self.locked_until.isoformat() if self.locked_until else None,
            "stripe_customer_id": self.stripe_customer_id,
        }


class Address(Base):
    """Address model - no soft delete needed, addresses are typically replaced"""
    __tablename__ = "addresses"
    __table_args__ = (
        Index('idx_addresses_user_id', 'user_id'),
        Index('idx_addresses_city', 'city'),
        Index('idx_addresses_state', 'state'),
        Index('idx_addresses_country', 'country'),
        Index('idx_addresses_post_code', 'post_code'),
        Index('idx_addresses_user_created', 'user_id', 'created_at'),
        {'schema': 'accounts'}
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), onupdate=func.now(), nullable=True)

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("accounts.users.id"))
    street: Mapped[str] = mapped_column(String(CHAR_LENGTH))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100))
    post_code: Mapped[str] = mapped_column(String(20))

    # Relationships
    user = relationship("User", back_populates="addresses")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "street": self.street,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "post_code": self.post_code,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
