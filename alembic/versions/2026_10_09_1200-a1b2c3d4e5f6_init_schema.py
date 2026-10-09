"""init: every table as the models define them, plus the seeded roles and permissions

Generated from the SQLAlchemy models (accounts and bookings schemas). The roles and the
<table>:<action> permissions are hardcoded below on purpose: a migration must not change when the
models do, so later models get their own migration.

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

ACTIONS = ("view", "create", "edit", "delete")
MODELS = ['bookings', 'hotels', 'permissions', 'reviews', 'roles', 'room_types', 'rooms', 'users']
SCOPES = [f"{m}:{a}" for m in MODELS for a in ACTIONS]
ROLES = ["guest", "customer", "support", "manager", "admin", "superadmin"]

roles = sa.table("roles", sa.column("id", core.db.GUID()), sa.column("name", sa.String), schema="accounts")
permissions = sa.table("permissions", sa.column("id", core.db.GUID()), sa.column("name", sa.String), schema="accounts")


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS accounts")
    op.execute("CREATE SCHEMA IF NOT EXISTS bookings")

    op.create_table('permissions',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    schema='accounts'
    )
    op.create_index(op.f('ix_accounts_permissions_created_at'), 'permissions', ['created_at'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_permissions_name'), 'permissions', ['name'], unique=True, schema='accounts')
    op.create_index(op.f('ix_accounts_permissions_updated_at'), 'permissions', ['updated_at'], unique=False, schema='accounts')
    op.create_table('roles',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    schema='accounts'
    )
    op.create_index(op.f('ix_accounts_roles_created_at'), 'roles', ['created_at'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_roles_name'), 'roles', ['name'], unique=True, schema='accounts')
    op.create_index(op.f('ix_accounts_roles_updated_at'), 'roles', ['updated_at'], unique=False, schema='accounts')
    op.create_table('hotels',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('state', sa.String(length=100), nullable=True),
    sa.Column('zip_code', sa.String(length=20), nullable=True),
    sa.Column('country', sa.String(length=100), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_hotels_city'), 'hotels', ['city'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_country'), 'hotels', ['country'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_created_at'), 'hotels', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_currency'), 'hotels', ['currency'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_name'), 'hotels', ['name'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_updated_at'), 'hotels', ['updated_at'], unique=False, schema='bookings')
    op.create_table('room_types',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('bed', sa.String(length=20), nullable=False),
    sa.Column('windows', sa.Integer(), nullable=False),
    sa.Column('doors', sa.Integer(), nullable=False),
    sa.Column('wardrobe', sa.Boolean(), nullable=False),
    sa.Column('desk_chair', sa.Boolean(), nullable=False),
    sa.Column('tv', sa.Boolean(), nullable=False),
    sa.Column('wifi', sa.Boolean(), nullable=False),
    sa.Column('safe', sa.Boolean(), nullable=False),
    sa.Column('minifridge', sa.Boolean(), nullable=False),
    sa.Column('kettle', sa.Boolean(), nullable=False),
    sa.Column('coffee_maker', sa.Boolean(), nullable=False),
    sa.Column('tea_maker', sa.Boolean(), nullable=False),
    sa.Column('room_service', sa.Boolean(), nullable=False),
    sa.Column('bottled_water', sa.Boolean(), nullable=False),
    sa.Column('bathrobe', sa.Boolean(), nullable=False),
    sa.Column('slippers', sa.Boolean(), nullable=False),
    sa.Column('hairdryer', sa.Boolean(), nullable=False),
    sa.Column('keycard', sa.Boolean(), nullable=False),
    sa.Column('seating_area', sa.Boolean(), nullable=False),
    sa.Column('bedside_lamp', sa.Boolean(), nullable=False),
    sa.Column('soundproofing', sa.Boolean(), nullable=False),
    sa.Column('jacuzzi', sa.Boolean(), nullable=False),
    sa.Column('kitchenette', sa.Boolean(), nullable=False),
    sa.Column('price_per_night', sa.Integer(), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_room_types_bed'), 'room_types', ['bed'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_types_created_at'), 'room_types', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_types_price_per_night'), 'room_types', ['price_per_night'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_types_updated_at'), 'room_types', ['updated_at'], unique=False, schema='bookings')
    op.create_table('role_permissions',
    sa.Column('role_id', core.db.GUID(), nullable=False),
    sa.Column('permission_id', core.db.GUID(), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['permission_id'], ['accounts.permissions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['accounts.roles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('role_id', 'permission_id', name='uq_role_permissions_role_permission'),
    schema='accounts'
    )
    op.create_index(op.f('ix_accounts_role_permissions_created_at'), 'role_permissions', ['created_at'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_role_permissions_permission_id'), 'role_permissions', ['permission_id'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_role_permissions_updated_at'), 'role_permissions', ['updated_at'], unique=False, schema='accounts')
    op.create_table('users',
    sa.Column('firstname', sa.String(length=255), nullable=False),
    sa.Column('middlename', sa.String(length=255), nullable=True),
    sa.Column('lastname', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('passwordhash', sa.String(length=255), nullable=False),
    sa.Column('account_status', sa.String(length=50), nullable=False),
    sa.Column('role_id', core.db.GUID(), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('country', sa.String(length=100), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=False),
    sa.Column('timezone', sa.String(length=100), nullable=True),
    sa.Column('avatar_url', sa.String(length=500), nullable=True),
    sa.Column('dob', core.db.UTCDateTime(), nullable=True),
    sa.Column('gender', sa.String(length=20), nullable=True),
    sa.Column('last_login', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['role_id'], ['accounts.roles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    schema='accounts'
    )
    op.create_index(op.f('ix_accounts_users_account_status'), 'users', ['account_status'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_country'), 'users', ['country'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_created_at'), 'users', ['created_at'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_dob'), 'users', ['dob'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_email'), 'users', ['email'], unique=True, schema='accounts')
    op.create_index(op.f('ix_accounts_users_gender'), 'users', ['gender'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_language'), 'users', ['language'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_last_login'), 'users', ['last_login'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_phone'), 'users', ['phone'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_role_id'), 'users', ['role_id'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_timezone'), 'users', ['timezone'], unique=False, schema='accounts')
    op.create_index(op.f('ix_accounts_users_updated_at'), 'users', ['updated_at'], unique=False, schema='accounts')
    op.create_table('rooms',
    sa.Column('hotel_id', core.db.GUID(), nullable=False),
    sa.Column('room_type_id', core.db.GUID(), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['hotel_id'], ['bookings.hotels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_type_id'], ['bookings.room_types.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('hotel_id', 'number', name='uq_rooms_hotel_number'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_rooms_created_at'), 'rooms', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_rooms_room_type_id'), 'rooms', ['room_type_id'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_rooms_updated_at'), 'rooms', ['updated_at'], unique=False, schema='bookings')
    op.create_table('bookings',
    sa.Column('hotel_id', core.db.GUID(), nullable=False),
    sa.Column('user_id', core.db.GUID(), nullable=False),
    sa.Column('room_id', core.db.GUID(), nullable=False),
    sa.Column('check_in', sa.Date(), nullable=False),
    sa.Column('check_out', sa.Date(), nullable=False),
    sa.Column('check_in_time', core.db.UTCDateTime(), nullable=True),
    sa.Column('check_out_time', core.db.UTCDateTime(), nullable=True),
    sa.Column('guests', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('total_price', sa.Integer(), nullable=False),
    sa.Column('discount', sa.Integer(), nullable=False),
    sa.Column('base_price', sa.Integer(), nullable=False),
    sa.Column('booking_code', sa.String(length=20), nullable=True),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('check_out > check_in', name='ck_bookings_dates'),
    sa.CheckConstraint('discount BETWEEN 0 AND 100', name='ck_bookings_discount'),
    sa.CheckConstraint('guests > 0', name='ck_bookings_guests'),
    sa.ForeignKeyConstraint(['hotel_id'], ['bookings.hotels.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['room_id'], ['bookings.rooms.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['accounts.users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_bookings_booking_code'), 'bookings', ['booking_code'], unique=True, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_check_in'), 'bookings', ['check_in'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_check_out'), 'bookings', ['check_out'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_created_at'), 'bookings', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_currency'), 'bookings', ['currency'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_hotel_id'), 'bookings', ['hotel_id'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_room_id'), 'bookings', ['room_id'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_status'), 'bookings', ['status'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_updated_at'), 'bookings', ['updated_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_bookings_user_id'), 'bookings', ['user_id'], unique=False, schema='bookings')
    op.create_table('reviews',
    sa.Column('booking_id', core.db.GUID(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('is_approved', sa.Boolean(), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_reviews_rating'),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.bookings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('booking_id'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_reviews_created_at'), 'reviews', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_reviews_is_approved'), 'reviews', ['is_approved'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_reviews_rating'), 'reviews', ['rating'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_reviews_updated_at'), 'reviews', ['updated_at'], unique=False, schema='bookings')

    # ---- seed: roles, scopes, and every scope for the superadmin ----
    op.bulk_insert(roles, [{"id": str(uuid.uuid4()), "name": n} for n in ROLES])
    op.bulk_insert(permissions, [{"id": str(uuid.uuid4()), "name": n} for n in SCOPES])
    op.execute(
        "INSERT INTO accounts.role_permissions (id, role_id, permission_id) "
        "SELECT gen_random_uuid(), r.id, p.id FROM accounts.roles r CROSS JOIN accounts.permissions p "
        "WHERE r.name = 'superadmin'"
    )


def downgrade():
    op.drop_table('reviews', schema='bookings')
    op.drop_table('bookings', schema='bookings')
    op.drop_table('rooms', schema='bookings')
    op.drop_table('users', schema='accounts')
    op.drop_table('role_permissions', schema='accounts')
    op.drop_table('room_types', schema='bookings')
    op.drop_table('hotels', schema='bookings')
    op.drop_table('roles', schema='accounts')
    op.drop_table('permissions', schema='accounts')
    op.execute("DROP SCHEMA IF EXISTS bookings")
    op.execute("DROP SCHEMA IF EXISTS accounts")
