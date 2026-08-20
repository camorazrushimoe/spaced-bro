"""Alembic migration environment.

The database URL comes exclusively from the ``DATABASE_URL`` environment
variable (see proposal/design: secrets via env only). ``target_metadata`` is
the shared declarative base so autogenerate sees all models.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from spacedbro.db.base import Base

config = context.config

DEFAULT_DATABASE_URL = "sqlite:////data/spacedbro.db"


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL).strip()
    return value or DEFAULT_DATABASE_URL


config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DB connection)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
