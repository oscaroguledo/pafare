"""init: accounts (roles, permissions, users) and bookings (hotels, rooms, bookings, reviews) with seeded roles and scopes

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-10-09 12:00:00
"""
import uuid
import sqlalchemy as sa
from alembic import op
import core.db

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None

# Snapshot, hardcoded on purpose: a migration must not change when the models do.
ACTIONS = ("view", "create", "edit", "delete")
MODELS = ["users", "roles", "permissions", "hotels", "room_types", "rooms", "bookings", "reviews"]
SCOPES = [f"{m}:{a}" for m in MODELS for a in ACTIONS]
ROLES = ["guest", "customer", "support", "manager", "admin", "superadmin"]

permissions = sa.table("permissions", sa.column("id", core.db.GUID()), sa.column("name", sa.String), schema="accounts")
roles = sa.table("roles", sa.column("id", core.db.GUID()), sa.column("name", sa.String), schema="accounts")
role_permissions = sa.table("role_permissions", sa.column("id", core.db.GUID()), sa.column("role_id", core.db.GUID()), sa.column("permission_id", core.db.GUID()), schema="accounts")


def _timestamps():
    return [
        sa.Column("created_at", core.db.UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", core.db.UTCDateTime(), server_default=sa.func.now(), nullable=True),
    ]


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS accounts")
    op.execute("CREATE SCHEMA IF NOT EXISTS bookings")

    op.create_table("roles",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        *_timestamps(), schema="accounts")
    op.create_index("ix_accounts_roles_name", "roles", ["name"], unique=True, schema="accounts")
    op.create_index("ix_accounts_roles_created_at", "roles", ["created_at"], schema="accounts")
    op.create_index("ix_accounts_roles_updated_at", "roles", ["updated_at"], schema="accounts")

    op.create_table("permissions",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        *_timestamps(), schema="accounts")
    op.create_index("ix_accounts_permissions_name", "permissions", ["name"], unique=True, schema="accounts")
    op.create_index("ix_accounts_permissions_created_at", "permissions", ["created_at"], schema="accounts")
    op.create_index("ix_accounts_permissions_updated_at", "permissions", ["updated_at"], schema="accounts")

    op.create_table("role_permissions",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("role_id", core.db.GUID(), sa.ForeignKey("accounts.roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_id", core.db.GUID(), sa.ForeignKey("accounts.permissions.id", ondelete="CASCADE"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
        schema="accounts")
    op.create_index("ix_accounts_role_permissions_permission_id", "role_permissions", ["permission_id"], schema="accounts")
    op.create_index("ix_accounts_role_permissions_created_at", "role_permissions", ["created_at"], schema="accounts")
    op.create_index("ix_accounts_role_permissions_updated_at", "role_permissions", ["updated_at"], schema="accounts")

    op.create_table("users",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("firstname", sa.String(255), nullable=False),
        sa.Column("middlename", sa.String(255), nullable=True),
        sa.Column("lastname", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("passwordhash", sa.String(255), nullable=False),
        sa.Column("account_status", sa.String(50), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("role_id", core.db.GUID(), sa.ForeignKey("accounts.roles.id"), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("dob", core.db.UTCDateTime(), nullable=True),
        sa.Column("gender", sa.String(20), nullable=True),
        sa.Column("last_login", core.db.UTCDateTime(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("email", name="uq_users_email"),
        schema="accounts")
    for col in ("lastname", "account_status", "verified", "role_id", "phone", "country", "language", "last_login", "created_at", "updated_at"):
        op.create_index(f"ix_accounts_users_{col}", "users", [col], schema="accounts")

    # ---- bookings ----
    op.create_table("hotels",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("state", sa.String(100), nullable=True),
        sa.Column("zip_code", sa.String(20), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="NGN"),
        *_timestamps(), schema="bookings")
    for col in ("name", "city", "country", "currency", "created_at", "updated_at"):
        op.create_index(f"ix_bookings_hotels_{col}", "hotels", [col], schema="bookings")

    op.create_table("room_types",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("bed", sa.String(20), nullable=False),
        sa.Column("windows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("doors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wardrobe", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("desk_chair", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tv", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("wifi", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("safe", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("minifridge", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("kettle", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("coffee_maker", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tea_maker", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("room_service", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("bottled_water", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("bathrobe", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("slippers", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hairdryer", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("keycard", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("seating_area", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("bedside_lamp", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("soundproofing", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("jacuzzi", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("kitchenette", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("price_per_night", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("name", name="uq_room_types_name"),
        sa.CheckConstraint("price_per_night >= 0", name="ck_room_types_price"),
        schema="bookings")
    for col in ("bed", "price_per_night", "created_at", "updated_at"):
        op.create_index(f"ix_bookings_room_types_{col}", "room_types", [col], schema="bookings")

    op.create_table("rooms",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("hotel_id", core.db.GUID(), sa.ForeignKey("bookings.hotels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("room_type_id", core.db.GUID(), sa.ForeignKey("bookings.room_types.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("hotel_id", "number", name="uq_rooms_hotel_number"),
        schema="bookings")
    for col in ("room_type_id", "created_at", "updated_at"):
        op.create_index(f"ix_bookings_rooms_{col}", "rooms", [col], schema="bookings")

    op.create_table("bookings",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("user_id", core.db.GUID(), sa.ForeignKey("accounts.users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("room_id", core.db.GUID(), sa.ForeignKey("bookings.rooms.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("check_out", sa.Date(), nullable=False),
        sa.Column("guests", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total_price", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.CheckConstraint("check_out > check_in", name="ck_bookings_dates"),
        sa.CheckConstraint("guests > 0", name="ck_bookings_guests"),
        schema="bookings")
    for col in ("user_id", "room_id", "check_in", "check_out", "status", "created_at", "updated_at"):
        op.create_index(f"ix_bookings_bookings_{col}", "bookings", [col], schema="bookings")

    op.create_table("reviews",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("booking_id", core.db.GUID(), sa.ForeignKey("bookings.bookings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default="false"),
        *_timestamps(),
        sa.UniqueConstraint("booking_id", name="uq_reviews_booking_id"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
        schema="bookings")
    for col in ("rating", "is_approved", "created_at", "updated_at"):
        op.create_index(f"ix_bookings_reviews_{col}", "reviews", [col], schema="bookings")

    # Seed roles, scopes, and give the superadmin every scope
    conn = op.get_bind()
    have_roles = {r[0] for r in conn.execute(sa.select(roles.c.name))}
    op.bulk_insert(roles, [{"id": str(uuid.uuid4()), "name": n} for n in ROLES if n not in have_roles])
    op.bulk_insert(permissions, [{"id": str(uuid.uuid4()), "name": n} for n in SCOPES])

    sa_id = conn.execute(sa.select(roles.c.id).where(roles.c.name == "superadmin")).scalar()
    op.bulk_insert(role_permissions, [
        {"id": str(uuid.uuid4()), "role_id": str(sa_id), "permission_id": str(pid)}
        for (pid,) in conn.execute(sa.select(permissions.c.id)).all()
    ])


def downgrade():
    for t in ("reviews", "bookings", "rooms", "room_types", "hotels"):
        op.drop_table(t, schema="bookings")
    op.drop_table("users", schema="accounts")
    op.drop_table("role_permissions", schema="accounts")
    op.drop_table("permissions", schema="accounts")
    op.drop_table("roles", schema="accounts")
    op.execute("DROP SCHEMA IF EXISTS bookings")
