"""Shared fixtures for repository-backed tests (BON-33 code review).

The ``session`` / ``clock`` / ``users`` / ``items`` fixtures were defined
verbatim in ``test_repositories.py`` and re-declared in
``test_scheduler.py`` — duplicated code. They live here, in a plain
fixture module imported by the test files (the same pattern as
``addflow_fixtures.py``), so the DB + frozen-clock setup has one home.

Everything runs against in-memory SQLite and the production
``FrozenClock`` — no wall clock, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from spacedbro.clock import FrozenClock
from spacedbro.db.base import Base
from spacedbro.db.engine import create_db_engine
from spacedbro.db.repositories import ItemRepository, UserRepository

#: Frozen "now" for the repository tests — 2026-08-21 12:00 UTC. Tests
#: that need a different instant build one with ``NOW + timedelta(...)``.
NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def session() -> Session:
    """In-memory SQLite session with the full schema (migration-built DDL
    lives in the migration; here we build from the shared metadata)."""
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
def users(session: Session, clock: FrozenClock) -> UserRepository:
    return UserRepository(session, clock)


@pytest.fixture
def items(session: Session, clock: FrozenClock) -> ItemRepository:
    return ItemRepository(session, clock)
