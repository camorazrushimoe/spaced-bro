"""Tests for the injectable UTC clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spacedbro.clock import FrozenClock, UtcClock


def test_utc_clock_returns_timezone_aware_utc() -> None:
    now = UtcClock().utc_now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_frozen_clock_returns_fixed_time() -> None:
    fixed = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(fixed)

    assert clock.utc_now() == fixed
    assert clock.utc_now() == fixed  # stays frozen, no drift


def test_frozen_clock_advance() -> None:
    fixed = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(fixed)

    clock.advance(minutes=20)

    assert clock.utc_now() == fixed + timedelta(minutes=20)


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 8, 20, 12, 0, 0))


async def test_utc_clock_sleep_advances_time() -> None:
    clock = UtcClock()

    before = clock.utc_now()
    await clock.sleep(0.01)
    after = clock.utc_now()

    assert after >= before + timedelta(microseconds=5_000)


async def test_frozen_clock_sleep_records_wait_and_advances() -> None:
    fixed = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(fixed)

    await clock.sleep(1.5)

    assert clock.utc_now() == fixed + timedelta(seconds=1.5)
    assert clock.waits == [1.5]

    await clock.sleep(0.25)
    assert clock.waits == [1.5, 0.25]
    assert clock.total_waited() == pytest.approx(1.75)
