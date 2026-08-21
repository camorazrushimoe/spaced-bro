"""Run Alembic migrations on startup, before any traffic is served."""

from __future__ import annotations

import logging
import os

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)


def run_migrations(database_url: str) -> None:
    """Apply all pending migrations to ``head``.

    ``alembic.ini`` is expected in the current working directory (the Docker
    image sets ``WORKDIR /app``), and ``alembic/env.py`` reads the URL from the
    ``DATABASE_URL`` environment variable rather than a committed file.
    """
    ini_path = os.environ.get("ALEMBIC_CONFIG", "alembic.ini")
    os.environ.setdefault("DATABASE_URL", database_url)

    config = Config(ini_path)
    command.upgrade(config, "head")
    logger.info("Database migrations applied (Alembic upgrade head)")
