"""SQLAlchemy declarative base shared by models and Alembic migrations."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
