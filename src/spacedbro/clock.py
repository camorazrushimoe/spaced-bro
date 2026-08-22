"""Injectable UTC clock.

All time-dependent code accepts a ``Clock`` so tests can freeze time and
production uses real UTC. See ``srs-engine/spec.md`` — "Injectable clock",
and ``llm-client/spec.md`` — "Test seam (normative)": the clock used for
timeouts and backoff MUST be injectable so boundary tests are deterministic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    """A source of the current UTC time and a way to wait on it."""

    def utc_now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` (in real time in production; recorded in tests)."""
        ...


class UtcClock:
    """The production clock — real UTC wall time."""

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FrozenClock:
    """A deterministic clock for tests.

    ``now`` must be timezone-aware; ``advance`` moves it forward by the given
    ``timedelta`` keyword arguments (e.g. ``minutes=20``). ``sleep`` records
    the requested wait in ``waits`` and advances the frozen time, so tests
    can assert attempt counts and backoff boundaries without real waiting.
    """

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = now
        self.waits: list[float] = []

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> datetime:
        """Move the frozen time forward and return the new value."""
        self._now = self._now + timedelta(**kwargs)
        return self._now

    async def sleep(self, seconds: float) -> None:
        """Record the wait and advance the frozen time (no real waiting)."""
        self.waits.append(seconds)
        self.advance(seconds=seconds)

    def total_waited(self) -> float:
        return sum(self.waits)
