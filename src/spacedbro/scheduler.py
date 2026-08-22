"""In-process APScheduler for proactive reviews (BON-33, design §8,
scheduler spec).

Every ``SCHEDULER_INTERVAL_MINUTES`` (default 5) the tick runs a **proactive
pass** over all users: for each user it applies, in order,

1. **Back-off** — skip if ``last_active_at`` is older than 14 days
   (scheduler spec "Back-off");
2. **Daily cap** — skip if ``proactive_count`` for today's UTC date already
   equals the user's cap. The cap scales with activity (design §8):
   0 messages in the last 7 days -> 1; >=3 distinct UTC hours active -> 3;
   else 2. The counter resets at UTC midnight (the repository rolls it
   over when the UTC date changes; "a day" is a UTC calendar date);
3. **Active window** — the peak active hour ±1, derived from the UTC
   activity histogram. **Cold start** (no histogram at all): 09:00–21:00
   UTC.

If the user survives the gates, has items in the **shared due queue**
(``next_review_at <= now`` — the same queue on-demand ``/review`` reads,
design §7), and is under the cap, the bot sends ONE short nudge reporting
how many cards are due. The nudge does NOT consume the queue: due items
stay due for on-demand review, and the rest beyond the cap wait for later
days (design §7 "Unattended due cards").

**On-demand reviews never increment ``proactive_count``** — only a
successful proactive send does (``UserRepository.record_proactive``,
called here AFTER the send succeeds, so a failed send is not counted and
the user gets the chance again next tick).

**Dry run** (``SCHEDULER_DRY_RUN=1``): the full pass runs (stats + debug
logs), nothing is sent and nothing is counted — the verification mode of
the ticket's smoke checklist ("proactive dry-run").

**Single-instance assumption:** the scheduler runs IN-PROCESS in the single
bot process — NO distributed lock in MVP; running two bot replicas would
double proactive sends and corrupt the daily counters. See the README
"Proactive scheduling (operator note)" (canonical operator text); the
Postgres/Redis + distributed-lock upgrade path is design §10, out of MVP
scope.

Data-model note (why the 7-day window is an approximation): the BON-29
schema stores ONE 24-bucket UTC histogram per user (no per-message
timestamps), so "0 messages in the last 7 days" is checked exactly via
``last_active_at < now - 7 days`` (-> cap 1), while the ">=3 distinct UTC
hours" rule is evaluated over the recorded history, not only the last
7 days. A user whose activity is stale but whose last touch is inside
7 days therefore keeps their historical cap — bounded by 3, so the
worst case is one extra nudge, and the 14-day back-off ends the
pattern. Precise per-day accounting is the documented upgrade path
(design §10: Postgres/Redis), out of MVP scope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from spacedbro.clock import Clock
from spacedbro.db.models import ACTIVITY_BUCKETS, normalize_activity_buckets
from spacedbro.db.repositories import ItemRepository, UserRepository

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")

# --- Design §8 constants ------------------------------------------------------

#: How often the proactive pass runs ("job every N minutes"; N is
#: configurable via ``SCHEDULER_INTERVAL_MINUTES``, default 5).
DEFAULT_INTERVAL_MINUTES = 5
#: Cold-start window: hours 9..20 (09:00–21:00 UTC, end exclusive).
COLD_START_FIRST_HOUR = 9
COLD_START_LAST_HOUR = 21
#: Cap levels (design §8: "1 if low activity, 2 medium, 3 high").
CAP_LOW = 1
CAP_MEDIUM = 2
CAP_HIGH = 3
#: Back-off: skip proactive when last_active_at < now - BACKOFF_DAYS (spec
#: "Back-off" — strictly older than 14 days).
BACKOFF_DAYS = 14
#: 7-day activity window for the cap's low-activity rule (design §8).
ACTIVITY_WINDOW_DAYS = 7
#: Number of distinct UTC hours that count as "high activity" (design §8:
#: "active >=3 distinct UTC hours in the last 7 days -> 3").
HIGH_ACTIVITY_HOURS = 3
#: Width of the UTC histogram — the shared constant (BON-29 schema).
HISTORY_HOURS = ACTIVITY_BUCKETS


#: Production sender signature: (telegram_id, nudge text) -> None.
#: ``aiogram.Bot.send_message`` satisfies it; tests inject a fake.
Sender = Callable[[int, str], Awaitable[None]]
#: Factory opening a repository-bound session per pass (each pass uses one
#: session; the repositories commit atomically per mutation).
SessionFactory = Callable[[], Session]


class ProactiveDecision:
    """Outcome of the per-user gate (why a user is / is not nudged)."""

    __slots__ = ("allowed", "reason")

    def __init__(self, allowed: bool, reason: str) -> None:
        self.allowed = allowed
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ProactiveDecision(allowed={self.allowed}, reason={self.reason!r})"


@dataclass(frozen=True)
class ProactiveStats:
    """Aggregate of one pass — the dry-run verification surface."""

    checked: int
    sent: int
    dry_run: bool
    skipped: dict[str, int]


# --- Pure decision helpers (frozen-clock unit tests) --------------------------


def activity_cap(
    buckets: Optional[list[int]], last_active_at: datetime, now: datetime
) -> int:
    """Daily proactive cap from activity (design §8).

    - 0 messages in the last 7 days -> ``CAP_LOW`` (1) — exact, via
      ``last_active_at``;
    - otherwise, >=3 distinct UTC hours with recorded activity -> ``CAP_HIGH``
      (3);
    - otherwise -> ``CAP_MEDIUM`` (2).

    The histogram holds no per-message timestamps, so the distinct-hours
    check runs over the recorded history (documented approximation — see
    module docstring); the 14-day back-off bounds its effect.
    """
    if last_active_at < now - timedelta(days=ACTIVITY_WINDOW_DAYS):
        return CAP_LOW
    counts = normalize_activity_buckets(buckets)
    if sum(counts) == 0:
        return CAP_LOW
    if sum(1 for c in counts if c > 0) >= HIGH_ACTIVITY_HOURS:
        return CAP_HIGH
    return CAP_MEDIUM


def active_window(buckets: Optional[list[int]]) -> set[int]:
    """UTC hours in which a proactive nudge is allowed.

    Cold start (no histogram / all zero): ``09:00–21:00 UTC`` (scheduler
    spec "Cold-start window"). Otherwise the user's peak active hour ±1
    ("the bot tries not to bother them when it is inconvenient" — the
    histogram is the recorded activity pattern).
    """
    counts = normalize_activity_buckets(buckets)
    if sum(counts) == 0:
        return set(range(COLD_START_FIRST_HOUR, COLD_START_LAST_HOUR))
    peak = max(range(HISTORY_HOURS), key=lambda h: (counts[h], -h))
    return {(peak - 1) % HISTORY_HOURS, peak, (peak + 1) % HISTORY_HOURS}


def is_backed_off(last_active_at: datetime, now: datetime) -> bool:
    """True when the user is in back-off (last touch strictly older than
    14 days — scheduler spec "Back-off")."""
    return last_active_at < now - timedelta(days=BACKOFF_DAYS)


def decide_proactive(
    *,
    last_active_at: datetime,
    buckets: Optional[list[int]],
    proactive_count_today: int,
    now: datetime,
) -> ProactiveDecision:
    """Apply the three gates in order: back-off, daily cap, active window.

    ``proactive_count_today`` is the number of proactive messages sent on
    ``now``'s UTC date — read through the repository seam
    (``UserRepository.proactive_count_today``), which owns the UTC-midnight
    reset.
    """
    if is_backed_off(last_active_at, now):
        return ProactiveDecision(False, "back-off (inactive >14d)")
    cap = activity_cap(buckets, last_active_at, now)
    if proactive_count_today >= cap:
        return ProactiveDecision(False, f"daily cap reached ({cap})")
    if now.hour not in active_window(buckets):
        return ProactiveDecision(False, "outside active window")
    return ProactiveDecision(True, "ok")


def render_nudge(due_count: int) -> str:
    """The short nudge: report how many cards are due + the entry action
    (design §7: on-demand states "how many due"; the nudge points to the
    same queue)."""
    plural = "s" if due_count != 1 else ""
    return (
        f"Bro, you've got {due_count} card{plural} due. "
        f"Tap /review when you're up for it \U0001F680"
    )


# --- The pass -----------------------------------------------------------------


async def run_proactive_pass(
    *,
    clock: Clock,
    users: UserRepository,
    items: ItemRepository,
    sender: Sender,
    dry_run: bool = False,
) -> ProactiveStats:
    """One proactive pass over all users.

    Order per user: gates (back-off -> cap -> window) -> shared due queue
    -> one nudge -> ``record_proactive`` (only after a successful send).
    Users with no due items are not nudged and spend nothing. A sender
    failure is logged and NOT counted, so the same user is retried on the
    next tick. Returns stats (the dry-run verification surface).
    """
    now = clock.utc_now()
    sent = 0
    checked = 0
    skipped: dict[str, int] = {}

    def _skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for user in users.all():
        checked += 1
        decision = decide_proactive(
            last_active_at=user.last_active_at,
            buckets=user.activity_hours_utc,
            proactive_count_today=users.proactive_count_today(user.id, now),
            now=now,
        )
        if not decision.allowed:
            _skip(decision.reason)
            logger.debug(
                "proactive: user %s skipped (%s)", user.telegram_id, decision.reason
            )
            continue

        due = items.due(user.id, now)
        if not due:
            _skip("no due items")
            continue

        text = render_nudge(len(due))
        if dry_run:
            logger.info(
                "proactive DRY-RUN: would nudge %s (%d due)", user.telegram_id, len(due)
            )
            _skip("dry-run")
            continue
        try:
            await sender(user.telegram_id, text)
        except Exception:
            logger.exception(
                "proactive: send to %s failed — not counted, retry next tick",
                user.telegram_id,
            )
            _skip("send failed")
            continue

        today_count = users.record_proactive(user.id, now)
        sent += 1
        logger.info(
            "proactive: nudged %s (%d due, today's count now %d)",
            user.telegram_id,
            len(due),
            today_count,
        )

    return ProactiveStats(
        checked=checked,
        sent=sent,
        dry_run=dry_run,
        skipped=skipped,
    )


# --- APScheduler wiring ---------------------------------------------------------


async def _proactive_tick(
    clock: Clock,
    sender: Sender,
    session_factory: SessionFactory,
    dry_run: bool,
) -> None:
    """The scheduled job: open ONE session, run the pass, close it.

    Pre-BON-33 this was a no-op placeholder (the rules were deferred to a
    later ticket); now it runs the real pass. A single session serves the
    whole pass because the repositories commit per mutation — the pass is
    the unit of work. The wiring (``sender`` / ``session_factory``) is
    required: the boot path always provides it (see ``spacedbro.__main__``).
    """
    session = session_factory()
    try:
        stats = await run_proactive_pass(
            clock=clock,
            users=UserRepository(session, clock),
            items=ItemRepository(session, clock),
            sender=sender,
            dry_run=dry_run,
        )
        logger.info(
            "proactive pass done: checked=%d sent=%d skipped=%s dry_run=%s",
            stats.checked,
            stats.sent,
            stats.skipped,
            stats.dry_run,
        )
    finally:
        session.close()


def build_scheduler(
    clock: Clock,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    *,
    sender: Sender,
    session_factory: SessionFactory,
    dry_run: bool = False,
) -> AsyncIOScheduler:
    """Create the in-process scheduler with the proactive review tick.

    Runs in the same event loop as the bot (single instance — see module
    docstring and the README operator note). The daily cap, cold-start
    window and back-off rules live in :func:`decide_proactive` /
    :func:`run_proactive_pass`; this only wires the interval trigger
    (``interval_minutes`` = N of "job every N minutes", design §8).
    ``build_scheduler`` never starts the loop — ``__main__`` owns the
    migrate -> ``scheduler.start()`` boot order.
    """
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        _proactive_tick,
        trigger=IntervalTrigger(minutes=interval_minutes, timezone=UTC),
        args=[clock, sender, session_factory, dry_run],
        id="proactive-tick",
        name="proactive review tick",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
