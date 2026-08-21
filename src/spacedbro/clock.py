"""Injectable UTC clock.

All time-dependent code accepts a ``Clock`` so tests can freeze time and
production uses real UTC. See ``srs-engine/spec.md`` — "Injectable clock".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    """A source of the current UTC time."""

    def utc_now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class UtcClock:
    """The production clock — real UTC wall time."""

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """A deterministic clock for tests.

    ``now`` must be timezone-aware; ``advance`` moves it forward by the given
    ``timedelta`` keyword arguments (e.g. ``minutes=20``).
    """

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = now

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> datetime:
        """Move the frozen time forward and return the new value."""
        self._now = self._now + timedelta(**kwargs)
        return self._now
