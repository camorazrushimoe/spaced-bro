"""Tests for the in-process APScheduler setup (BON-27, acceptance criterion
"In-process APScheduler")."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from spacedbro.clock import FrozenClock
from spacedbro.scheduler import UTC, _proactive_tick, build_scheduler

FROZEN = FrozenClock(datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))


def test_build_scheduler_returns_asyncio_scheduler_in_utc() -> None:
    scheduler = build_scheduler(FROZEN)

    assert isinstance(scheduler, AsyncIOScheduler)
    assert scheduler.timezone == UTC


def test_build_scheduler_registers_proactive_tick_job() -> None:
    scheduler = build_scheduler(FROZEN)

    job = scheduler.get_job("proactive-tick")
    assert job is not None
    assert job.func is _proactive_tick
    # APScheduler normalizes positional args to a tuple.
    assert tuple(job.args) == (FROZEN,)
    # build_scheduler must not start the loop itself — __main__ owns the
    # migrate -> scheduler.start() boot order.
    assert not scheduler.running


def test_proactive_tick_uses_interval_trigger_in_utc() -> None:
    scheduler = build_scheduler(FROZEN)
    job = scheduler.get_job("proactive-tick")

    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.timezone == UTC
    assert job.trigger.interval == timedelta(minutes=5)  # default


def test_custom_interval_is_respected() -> None:
    scheduler = build_scheduler(FROZEN, interval_minutes=30)
    job = scheduler.get_job("proactive-tick")

    assert job.trigger.interval == timedelta(minutes=30)
    assert job.trigger.timezone == UTC
