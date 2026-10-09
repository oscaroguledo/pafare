from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, create_engine, text
from sqlalchemy import pool
from core.db import Base
from core.config import settings
from alembic import context
from models import *  # Import all models to register them with Alembic

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Only track our application schemas, not Supabase internals
APP_SCHEMAS = {'accounts', 'catalog', 'commerce', 'admin', 'system', 'public'}

def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        schema = getattr(object, "schema", None)
        return schema in APP_SCHEMAS
    return True

# Read DB URL from environment via core.config (overrides alembic.ini)
def get_url():
    return settings.SQLALCHEMY_DATABASE_URI_SYNC


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        get_url(),
        poolclass=pool.NullPool,
        connect_args={"options": "-csearch_path=accounts,catalog,commerce,admin,system,public"}
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
