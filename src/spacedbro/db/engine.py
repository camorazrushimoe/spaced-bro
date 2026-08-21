"""SQLite engine and session factory."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str) -> Engine:
    """Build a sync SQLAlchemy engine for the configured database URL.

    For SQLite, ``PRAGMA foreign_keys=ON`` is enabled on every connection —
    SQLite does not enforce foreign keys by default, and the schema
    (``learning_items.user_id -> users.id``) relies on the FK +
    ``ON DELETE CASCADE`` to keep per-user data isolated.
    """
    engine = create_engine(database_url, future=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):  # pragma: no cover - thin
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
