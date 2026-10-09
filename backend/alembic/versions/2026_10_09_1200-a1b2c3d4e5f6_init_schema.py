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

# Snapshot of core/scopes.py at the time of this migration, hardcoded on purpose: a migration must not change
# when the code does. Later scopes get their own migration.
SCOPES = {
    'users:view': 'List and read users',
    'users:create': 'Create users with any role',
    'users:edit': "Edit other users' profile fields",
    'users:change_email': "Change a user's email address",
    'users:reset_password': 'Send a user a password-reset link',
    'users:activate': 'Activate an inactive account',
    'users:deactivate': 'Deactivate an account',
    'users:suspend': 'Suspend an account',
    'users:unsuspend': 'Lift an account suspension',
    'roles:view': 'List and read roles and their permissions',
    'roles:create': 'Create roles',
    'roles:edit': 'Rename roles and edit their description',
    'roles:delete': 'Delete roles',
    'roles:assign': 'Give a user a different role',
    'roles:grant': 'Change which permissions a role has',
    'permissions:view': 'List and read permissions',
    'permissions:create': 'Create permissions',
    'permissions:edit': "Edit a permission's description",
    'permissions:delete': 'Delete permissions',
    'hotels:create': 'Create hotels',
    'hotels:edit': 'Edit hotels',
    'hotels:delete': 'Delete hotels',
    'hotels:access_all': 'Work on every hotel, not just the ones assigned to you',
    'hotels:assign_staff': 'Assign staff to a hotel or remove them',
    'room_types:create': 'Create room types',
    'room_types:edit': 'Edit room types',
    'room_types:delete': 'Delete room types',
    'room_rates:create': 'Create seasonal and weekday price rules',
    'room_rates:edit': 'Edit price rules',
    'room_rates:delete': 'Delete price rules',
    'rooms:create': 'Create rooms',
    'rooms:edit': 'Edit rooms',
    'rooms:delete': 'Delete rooms',
    'bookings:view': "List and read guests' bookings at your hotels",
    'bookings:create': 'Book a room on behalf of a guest',
    'bookings:edit': "Reschedule or change other guests' bookings",
    'bookings:cancel': "Cancel other guests' bookings",
    'bookings:confirm': 'Confirm a pending booking',
    'bookings:check_in': 'Check a guest in',
    'bookings:check_out': 'Check a guest out',
    'bookings:no_show': 'Mark a booking as a no-show',
    'bookings:discount': 'Apply a discount to a booking',
    'reviews:view': 'List and read all reviews, including unapproved ones',
    'reviews:edit': "Edit other guests' reviews",
    'reviews:delete': 'Delete reviews',
    'reviews:moderate': 'Approve or hide reviews',
    'payments:view': "List and read everyone's payments",
    'payments:confirm': 'Record that a payment was received',
    'payments:fail': 'Record that a payment failed',
    'payments:refund': 'Refund a payment',
}
ROLES = ["guest", "customer", "support", "manager", "admin", "superadmin"]

roles = sa.table("roles", sa.column("id", core.db.GUID()), sa.column("name", sa.String), schema="accounts")
permissions = sa.table("permissions", sa.column("id", core.db.GUID()), sa.column("name", sa.String), sa.column("description", sa.String), schema="accounts")


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS accounts")
    op.execute("CREATE SCHEMA IF NOT EXISTS billings")
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
    sa.Column('tax_percent', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('free_cancellation_hours', sa.Integer(), nullable=False),
    sa.Column('late_cancellation_fee_percent', sa.Integer(), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('free_cancellation_hours >= 0', name='ck_hotels_free_cancellation_hours'),
    sa.CheckConstraint('late_cancellation_fee_percent BETWEEN 0 AND 100', name='ck_hotels_late_cancellation_fee'),
    sa.CheckConstraint('tax_percent BETWEEN 0 AND 100', name='ck_hotels_tax_percent'),
    sa.PrimaryKeyConstraint('id'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_hotels_city'), 'hotels', ['city'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_country'), 'hotels', ['country'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_created_at'), 'hotels', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_currency'), 'hotels', ['currency'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_name'), 'hotels', ['name'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotels_updated_at'), 'hotels', ['updated_at'], unique=False, schema='bookings')
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
    op.create_table('room_types',
    sa.Column('hotel_id', core.db.GUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('bed', sa.String(length=20), nullable=False),
    sa.Column('max_guests', sa.Integer(), nullable=False),
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
    sa.CheckConstraint('max_guests >= 1', name='ck_room_types_max_guests'),
    sa.CheckConstraint('price_per_night >= 0', name='ck_room_types_price'),
    sa.ForeignKeyConstraint(['hotel_id'], ['bookings.hotels.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('hotel_id', 'name', name='uq_room_types_hotel_name'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_room_types_bed'), 'room_types', ['bed'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_types_created_at'), 'room_types', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_types_max_guests'), 'room_types', ['max_guests'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_types_price_per_night'), 'room_types', ['price_per_night'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_types_updated_at'), 'room_types', ['updated_at'], unique=False, schema='bookings')
    op.create_table('hotel_staff',
    sa.Column('hotel_id', core.db.GUID(), nullable=False),
    sa.Column('user_id', core.db.GUID(), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['hotel_id'], ['bookings.hotels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['accounts.users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('hotel_id', 'user_id', name='uq_hotel_staff_hotel_user'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_hotel_staff_created_at'), 'hotel_staff', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotel_staff_updated_at'), 'hotel_staff', ['updated_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_hotel_staff_user_id'), 'hotel_staff', ['user_id'], unique=False, schema='bookings')
    op.create_table('room_rates',
    sa.Column('room_type_id', core.db.GUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('weekdays', sa.String(length=20), nullable=True),
    sa.Column('price_per_night', sa.Integer(), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('end_date IS NULL OR start_date IS NULL OR end_date >= start_date', name='ck_room_rates_dates'),
    sa.CheckConstraint('price_per_night >= 0', name='ck_room_rates_price'),
    sa.ForeignKeyConstraint(['room_type_id'], ['bookings.room_types.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    schema='bookings'
    )
    op.create_index(op.f('ix_bookings_room_rates_created_at'), 'room_rates', ['created_at'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_rates_room_type_id'), 'room_rates', ['room_type_id'], unique=False, schema='bookings')
    op.create_index(op.f('ix_bookings_room_rates_updated_at'), 'room_rates', ['updated_at'], unique=False, schema='bookings')
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
    sa.Column('base_price', sa.Integer(), nullable=False),
    sa.Column('discount', sa.Integer(), nullable=False),
    sa.Column('tax_percent', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('tax_amount', sa.Integer(), nullable=False),
    sa.Column('total_price', sa.Integer(), nullable=False),
    sa.Column('cancelled_at', core.db.UTCDateTime(), nullable=True),
    sa.Column('cancellation_fee', sa.Integer(), nullable=False),
    sa.Column('refund_amount', sa.Integer(), nullable=False),
    sa.Column('booking_code', sa.String(length=20), nullable=True),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('cancellation_fee >= 0 AND refund_amount >= 0', name='ck_bookings_cancellation_amounts'),
    sa.CheckConstraint('check_out > check_in', name='ck_bookings_dates'),
    sa.CheckConstraint('discount BETWEEN 0 AND 100', name='ck_bookings_discount'),
    sa.CheckConstraint('guests > 0', name='ck_bookings_guests'),
    sa.CheckConstraint('tax_percent BETWEEN 0 AND 100', name='ck_bookings_tax_percent'),
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
    op.create_table('payments',
    sa.Column('user_id', core.db.GUID(), nullable=False),
    sa.Column('booking_id', core.db.GUID(), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('refunded_amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('card_id', sa.String(length=100), nullable=True),
    sa.Column('paid_at', core.db.UTCDateTime(), nullable=True),
    sa.Column('id', core.db.GUID(), nullable=False),
    sa.Column('created_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', core.db.UTCDateTime(), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('amount >= 0', name='ck_payments_amount'),
    sa.CheckConstraint('refunded_amount >= 0 AND refunded_amount <= amount', name='ck_payments_refunded_amount'),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.bookings.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['accounts.users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('reference'),
    schema='billings'
    )
    op.create_index(op.f('ix_billings_payments_booking_id'), 'payments', ['booking_id'], unique=False, schema='billings')
    op.create_index(op.f('ix_billings_payments_created_at'), 'payments', ['created_at'], unique=False, schema='billings')
    op.create_index(op.f('ix_billings_payments_status'), 'payments', ['status'], unique=False, schema='billings')
    op.create_index(op.f('ix_billings_payments_updated_at'), 'payments', ['updated_at'], unique=False, schema='billings')
    op.create_index(op.f('ix_billings_payments_user_id'), 'payments', ['user_id'], unique=False, schema='billings')
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
    op.bulk_insert(permissions, [{"id": str(uuid.uuid4()), "name": n, "description": d} for n, d in SCOPES.items()])
    op.execute(
        "INSERT INTO accounts.role_permissions (id, role_id, permission_id) "
        "SELECT gen_random_uuid(), r.id, p.id FROM accounts.roles r CROSS JOIN accounts.permissions p "
        "WHERE r.name = 'superadmin'"
    )


def downgrade():
    op.drop_table('reviews', schema='bookings')
    op.drop_table('payments', schema='billings')
    op.drop_table('bookings', schema='bookings')
    op.drop_table('rooms', schema='bookings')
    op.drop_table('room_rates', schema='bookings')
    op.drop_table('hotel_staff', schema='bookings')
    op.drop_table('room_types', schema='bookings')
    op.drop_table('users', schema='accounts')
    op.drop_table('role_permissions', schema='accounts')
    op.drop_table('hotels', schema='bookings')
    op.drop_table('roles', schema='accounts')
    op.drop_table('permissions', schema='accounts')
    op.execute("DROP SCHEMA IF EXISTS bookings")
    op.execute("DROP SCHEMA IF EXISTS billings")
    op.execute("DROP SCHEMA IF EXISTS accounts")
