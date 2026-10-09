"""init: roles, permissions, role_permissions, users and the seeded scopes

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
MODELS = ["users", "roles", "permissions"]
SCOPES = [f"{m}:{a}" for m in MODELS for a in ACTIONS]
ROLES = [("guest", 0), ("customer", 0), ("support", 1), ("manager", 2), ("admin", 3), ("superadmin", 4)]

permissions = sa.table("permissions", sa.column("id", core.db.GUID()), sa.column("name", sa.String), schema="accounts")
roles = sa.table("roles", sa.column("id", core.db.GUID()), sa.column("name", sa.String), sa.column("rank", sa.Integer), schema="accounts")
role_permissions = sa.table("role_permissions", sa.column("id", core.db.GUID()), sa.column("role_id", core.db.GUID()), sa.column("permission_id", core.db.GUID()), schema="accounts")


def _timestamps():
    return [
        sa.Column("created_at", core.db.UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", core.db.UTCDateTime(), server_default=sa.func.now(), nullable=True),
    ]


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS accounts")

    op.create_table("roles",
        sa.Column("id", core.db.GUID(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
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

    # Seed roles, scopes, and give the superadmin every scope
    conn = op.get_bind()
    have_roles = {r[0] for r in conn.execute(sa.select(roles.c.name))}
    op.bulk_insert(roles, [{"id": str(uuid.uuid4()), "name": n, "rank": r} for n, r in ROLES if n not in have_roles])
    op.bulk_insert(permissions, [{"id": str(uuid.uuid4()), "name": n} for n in SCOPES])

    sa_id = conn.execute(sa.select(roles.c.id).where(roles.c.name == "superadmin")).scalar()
    op.bulk_insert(role_permissions, [
        {"id": str(uuid.uuid4()), "role_id": str(sa_id), "permission_id": str(pid)}
        for (pid,) in conn.execute(sa.select(permissions.c.id)).all()
    ])


def downgrade():
    op.drop_table("users", schema="accounts")
    op.drop_table("role_permissions", schema="accounts")
    op.drop_table("permissions", schema="accounts")
    op.drop_table("roles", schema="accounts")
