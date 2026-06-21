"""Alembic environment for the customer identity PostgreSQL database."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.services.identity.models import IdentityBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not settings.identity_database_url:
    raise RuntimeError("IDENTITY_DATABASE_URL is required for Alembic migrations")

config.set_main_option("sqlalchemy.url", settings.identity_database_url)
target_metadata = IdentityBase.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.identity_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
