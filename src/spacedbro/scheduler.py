"""In-process APScheduler for proactive reviews (single instance, MVP)."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from spacedbro.clock import Clock

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")


def build_scheduler(clock: Clock, interval_minutes: int = 5) -> AsyncIOScheduler:
    """Create the in-process scheduler with the proactive review tick.

    Runs in the same event loop as the bot. The daily cap, cold-start window,
    and back-off rules are implemented by later tickets; the tick is a no-op
    placeholder here that records the current UTC time via the injected clock.
    """
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        _proactive_tick,
        trigger=IntervalTrigger(minutes=interval_minutes, timezone=UTC),
        args=[clock],
        id="proactive-tick",
        name="proactive review tick",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler


def _proactive_tick(clock: Clock) -> None:
    logger.debug("Proactive review tick at %s", clock.utc_now().isoformat())
