"""Proactive scheduler tests (BON-33, design §8, scheduler spec).

Covers the acceptance criteria of the ticket:

- APScheduler plumbing: in-process AsyncIOScheduler, interval trigger in
  UTC, "every N minutes" (default 5, configurable) — the BON-27 acceptance
  criterion "In-process APScheduler", now wired to the real pass.
- Cap scaling (design §8): 0 messages in the last 7 days -> 1; >=3 distinct
  UTC hours active -> 3; else 2. Expected values are the worked examples
  from the design.
- UTC day boundary: ``proactive_count`` resets at UTC midnight (BON-29
  repository + the scheduler's re-derivation on every tick).
- On-demand exclusion: on-demand reviews never touch ``proactive_count``;
  the proactive budget is spent only by proactive sends.
- Cap reached -> no further proactive until the next UTC date.
- Cold start (no histogram) -> proactive only 09:00-21:00 UTC.
- Back-off: ``last_active_at`` older than 14 days -> skip.
- Unattended due items: a proactive nudge does NOT consume the due queue —
  the same due items remain for on-demand review (design §7).
- Dry-run mode: the full pass runs, nothing is sent, nothing is counted.

All time is frozen (``FrozenClock`` from the BON-27 injectable clock) and
the databases are in-memory SQLite — no wall clock, no network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from spacedbro.clock import FrozenClock
from spacedbro.db.models import User
from spacedbro.db.repositories import ItemRepository, UserRepository
from spacedbro.scheduler import (
    BACKOFF_DAYS,
    CAP_HIGH,
    CAP_LOW,
    CAP_MEDIUM,
    COLD_START_FIRST_HOUR,
    COLD_START_LAST_HOUR,
    DEFAULT_INTERVAL_MINUTES,
    _proactive_tick,
    activity_cap,
    active_window,
    build_scheduler,
    decide_proactive,
    is_backed_off,
    render_nudge,
    run_proactive_pass,
)
from spacedbro.scheduler import ProactiveStats

UTC = timezone.utc

from .repo_fixtures import NOW, clock, items, session, users  # noqa: E402,F401


# --- APScheduler plumbing (BON-27 acceptance: in-process APScheduler) -------

def test_build_scheduler_returns_asyncio_scheduler_in_utc() -> None:
    clock = FrozenClock(NOW)
    sender = _Sender()
    factory = lambda: Session()  # noqa: E731 - wiring is not executed here
    scheduler = build_scheduler(
        clock, sender=sender, session_factory=factory
    )

    assert isinstance(scheduler, AsyncIOScheduler)
    assert scheduler.timezone is not None
    assert str(scheduler.timezone) == "UTC"


def test_build_scheduler_registers_proactive_tick_job() -> None:
    clock = FrozenClock(NOW)
    sender = _Sender()
    factory = lambda: Session()  # noqa: E731 - wiring is not executed here
    scheduler = build_scheduler(clock, sender=sender, session_factory=factory)

    job = scheduler.get_job("proactive-tick")
    assert job is not None
    assert job.func is _proactive_tick
    # The frozen clock is the first positional argument; the rest wire the
    # pass (sender / session factory / dry-run).
    assert tuple(job.args)[0] is clock
    assert tuple(job.args)[1] is sender
    # build_scheduler must not start the loop itself — __main__ owns the
    # migrate -> scheduler.start() boot order.
    assert not scheduler.running


def test_proactive_tick_uses_interval_trigger_in_utc() -> None:
    clock = FrozenClock(NOW)
    sender = _Sender()
    factory = lambda: Session()  # noqa: E731 - wiring is not executed here
    scheduler = build_scheduler(clock, sender=sender, session_factory=factory)
    job = scheduler.get_job("proactive-tick")

    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.timezone is not None
    assert str(job.trigger.timezone) == "UTC"
    assert job.trigger.interval == timedelta(minutes=DEFAULT_INTERVAL_MINUTES)


def test_custom_interval_is_respected() -> None:
    clock = FrozenClock(NOW)
    sender = _Sender()
    factory = lambda: Session()  # noqa: E731 - wiring is not executed here
    scheduler = build_scheduler(
        clock, interval_minutes=30, sender=sender, session_factory=factory
    )
    job = scheduler.get_job("proactive-tick")

    assert job.trigger.interval == timedelta(minutes=30)


# --- Cap scaling (design §8: 1 low / 2 medium / 3 high) ----------------------


def test_activity_cap_zero_messages_last_7_days_is_one() -> None:
    # Last message 8 days ago -> 0 messages in the last 7 days -> cap 1,
    # even if older activity touched many hours.
    buckets = [1] * 24
    last_active = NOW - timedelta(days=8)

    assert activity_cap(buckets, last_active, NOW) == CAP_LOW == 1


def test_activity_cap_no_activity_at_all_is_one() -> None:
    assert activity_cap(None, NOW, NOW) == CAP_LOW
    assert activity_cap([0] * 24, NOW, NOW) == CAP_LOW


def test_activity_cap_one_to_two_distinct_hours_is_two() -> None:
    one_hour = [0] * 24
    one_hour[12] = 40
    two_hours = [0] * 24
    two_hours[9] = 10
    two_hours[10] = 30

    assert activity_cap(one_hour, NOW, NOW) == CAP_MEDIUM == 2
    assert activity_cap(two_hours, NOW, NOW) == CAP_MEDIUM == 2


def test_activity_cap_three_distinct_hours_is_three() -> None:
    three_hours = [0] * 24
    three_hours[9] = 5
    three_hours[12] = 5
    three_hours[20] = 5

    assert activity_cap(three_hours, NOW, NOW) == CAP_HIGH == 3


def test_activity_cap_boundary_exactly_7_days_still_counts() -> None:
    # Last message exactly 7 days ago: not "0 messages in the last 7 days".
    buckets = [0] * 24
    buckets[12] = 3
    last_active = NOW - timedelta(days=7)

    assert activity_cap(buckets, last_active, NOW) == CAP_MEDIUM


# --- Active window (peak hour ±1; cold start 09:00-21:00 UTC) ----------------


def test_cold_start_window_is_09_to_21_utc_exclusive_end() -> None:
    assert active_window(None) == set(range(COLD_START_FIRST_HOUR, COLD_START_LAST_HOUR))
    assert active_window([0] * 24) == set(range(9, 21))
    # 09:00 in, 21:00 out (the spec window is 09:00-21:00).
    assert 9 in active_window(None)
    assert 20 in active_window(None)
    assert 21 not in active_window(None)
    assert 8 not in active_window(None)


def test_window_is_peak_hour_plus_minus_one() -> None:
    buckets = [0] * 24
    buckets[10] = 40
    buckets[11] = 2
    buckets[12] = 1

    assert active_window(buckets) == {9, 10, 11}


def test_window_wraps_midnight() -> None:
    buckets = [0] * 24
    buckets[23] = 30

    assert active_window(buckets) == {22, 23, 0}

    buckets[0] = 30
    buckets[23] = 0

    assert active_window(buckets) == {23, 0, 1}


def test_window_ties_resolve_to_earlier_hour() -> None:
    buckets = [0] * 24
    buckets[5] = 3
    buckets[2] = 3

    assert active_window(buckets) == {1, 2, 3}


# --- Back-off (design §8: last_active_at < now - 14 days -> skip) -------------


def test_back_off_is_strictly_older_than_14_days() -> None:
    assert not is_backed_off(NOW - timedelta(days=BACKOFF_DAYS), NOW)
    assert is_backed_off(NOW - timedelta(days=BACKOFF_DAYS, seconds=1), NOW)
    assert not is_backed_off(NOW, NOW)


# --- decide_proactive: the composed gate --------------------------------------


def _hot_buckets(peak_hour: int = 12) -> list[int]:
    """3 distinct hours around the peak -> cap 3, window peak ±1."""
    buckets = [0] * 24
    for h in (peak_hour - 1, peak_hour, peak_hour + 1):
        buckets[h % 24] = 5
    return buckets


def test_decide_allows_healthy_user_in_window_under_cap() -> None:
    decision = decide_proactive(
        last_active_at=NOW - timedelta(hours=2),
        buckets=_hot_buckets(12),
        proactive_count_today=1,
        now=NOW,
    )

    assert decision.allowed
    assert decision.reason == "ok"


def test_decide_back_off_skips_inactive_user() -> None:
    decision = decide_proactive(
        last_active_at=NOW - timedelta(days=15),
        buckets=_hot_buckets(12),
        proactive_count_today=0,
        now=NOW,
    )

    assert not decision.allowed
    assert "back-off" in decision.reason


def test_decide_cap_reached_blocks_until_next_utc_date() -> None:
    # cap 3 (three distinct hours) and 3 already sent today -> blocked.
    decision = decide_proactive(
        last_active_at=NOW - timedelta(hours=1),
        buckets=_hot_buckets(12),
        proactive_count_today=3,
        now=NOW,
    )

    assert not decision.allowed
    assert "cap" in decision.reason


def test_decide_one_under_cap_still_allowed() -> None:
    decision = decide_proactive(
        last_active_at=NOW - timedelta(hours=1),
        buckets=_hot_buckets(12),
        proactive_count_today=2,
        now=NOW,
    )

    assert decision.allowed


def test_decide_outside_active_window_blocks() -> None:
    # Window {11,12,13}; 15:00 UTC is outside.
    decision = decide_proactive(
        last_active_at=NOW - timedelta(hours=1),
        buckets=_hot_buckets(12),
        proactive_count_today=0,
        now=NOW + timedelta(hours=3),
    )

    assert not decision.allowed
    assert "window" in decision.reason


def test_decide_cold_start_only_within_09_21_utc() -> None:
    cold = [0] * 24
    assert decide_proactive(last_active_at=NOW, buckets=cold, proactive_count_today=0, now=NOW - timedelta(hours=3, minutes=1)).allowed is False
    assert decide_proactive(last_active_at=NOW, buckets=cold, proactive_count_today=0, now=NOW - timedelta(hours=3)).allowed is True
    assert decide_proactive(last_active_at=NOW, buckets=cold, proactive_count_today=0, now=NOW + timedelta(hours=8, minutes=59)).allowed is True
    assert decide_proactive(last_active_at=NOW, buckets=cold, proactive_count_today=0, now=NOW + timedelta(hours=9)).allowed is False


# --- run_proactive_pass: real repositories + frozen clock ---------------------


class _Sender:
    """Records proactive sends; can be made to fail on demand."""

    def __init__(self, fail_for: frozenset[int] = frozenset()) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail_for = fail_for

    async def __call__(self, telegram_id: int, text: str) -> None:
        if telegram_id in self.fail_for:
            raise RuntimeError("telegram down")
        self.sent.append((telegram_id, text))


def _seed_user(
    users: UserRepository,
    session: Session,
    clock: FrozenClock,
    telegram_id: int,
    *,
    buckets: list[int] | None = None,
    last_active: datetime | None = None,
    count: int = 0,
    count_date: date | None = None,
) -> int:
    uid = users.get_or_create(telegram_id, clock.utc_now())
    user = session.get(User, uid)
    assert user is not None
    if buckets is not None:
        user.activity_hours_utc = buckets
    if last_active is not None:
        user.last_active_at = last_active
    user.proactive_count = count
    user.proactive_count_date = count_date
    session.commit()
    return uid


def _seed_due_item(
    items: ItemRepository,
    uid: int,
    *,
    front: str = "hello",
    due_at: datetime | None = None,
) -> int:
    # Due in the PAST by default (the card matured ~30 min before the
    # frozen NOW) — the due queue is next_review_at <= now.
    if due_at is None:
        due_at = NOW - timedelta(minutes=30)
    item = items.save(
        uid,
        front,
        "meaning",
        next_review_at=due_at,
        now=NOW,
    )
    return item.id


async def _pass(
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
    sender: _Sender,
    *,
    dry_run: bool = False,
) -> ProactiveStats:
    """Drive the async pass from the (sync-bodied) tests."""
    return await run_proactive_pass(
        clock=clock, users=users, items=items, sender=sender, dry_run=dry_run
    )


async def test_pass_nudges_due_user_and_counts_one_proactive(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    uid = _seed_user(users, session, clock, 101, buckets=_hot_buckets(12))
    item_id = _seed_due_item(items, uid)
    sender = _Sender()

    stats = await _pass(clock, users, items, sender)

    assert len(sender.sent) == 1
    telegram_id, text = sender.sent[0]
    assert telegram_id == 101
    assert "1" in text  # reports the due count
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 1
    assert user.proactive_count_date == NOW.date()
    assert stats.sent == 1
    # The nudge does NOT consume the due queue (design §7): the card is
    # still due for on-demand review, untouched.
    due = items.due(uid, clock.utc_now())
    assert [i.id for i in due] == [item_id]
    item = items.get(uid, item_id)
    assert item is not None
    assert item.next_review_at == NOW - timedelta(minutes=30)  # untouched


async def test_pass_skips_user_with_no_due_items(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    _seed_user(users, session, clock, 102, buckets=_hot_buckets(12))
    sender = _Sender()

    stats = await _pass(clock, users, items, sender)

    assert sender.sent == []
    assert stats.sent == 0
    assert stats.skipped.get("no due items") == 1


async def test_pass_skips_user_at_cap(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    uid = _seed_user(
        users,
        session,
        clock,
        103,
        buckets=_hot_buckets(12),  # cap 3
        count=3,
        count_date=NOW.date(),
    )
    _seed_due_item(items, uid)
    sender = _Sender()

    await _pass(clock, users, items, sender)

    assert sender.sent == []
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 3  # untouched


async def test_pass_cap_resets_at_utc_midnight(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    # 3 sent YESTERDAY (UTC date); today the budget restarts.
    uid = _seed_user(
        users,
        session,
        clock,
        104,
        buckets=_hot_buckets(12),
        count=3,
        count_date=NOW.date() - timedelta(days=1),  # cap spent YESTERDAY
    )
    _seed_due_item(items, uid)
    sender = _Sender()

    stats = await _pass(clock, users, items, sender)

    assert stats.sent == 1
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 1  # reset, not 4
    assert user.proactive_count_date == NOW.date()


async def test_pass_skips_backed_off_user(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    uid = _seed_user(
        users,
        session,
        clock,
        105,
        buckets=_hot_buckets(12),
        last_active=NOW - timedelta(days=15),
    )
    _seed_due_item(items, uid)
    sender = _Sender()

    await _pass(clock, users, items, sender)

    assert sender.sent == []


async def test_pass_skips_user_outside_active_window(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    late = FrozenClock(NOW + timedelta(hours=3))  # window {11,12,13}
    uid = _seed_user(users, session, late, 106, buckets=_hot_buckets(12))
    _seed_due_item(items, uid)
    sender = _Sender()

    await _pass(late, users, items, sender)

    assert sender.sent == []


async def test_pass_cold_start_respects_09_21_window(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    late = FrozenClock(NOW + timedelta(hours=10))
    uid = _seed_user(users, session, late, 107)  # zero histogram -> cold start
    _seed_due_item(items, uid)
    sender = _Sender()

    await _pass(late, users, items, sender)

    assert sender.sent == []


async def test_pass_dry_run_sends_and_counts_nothing(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    uid = _seed_user(users, session, clock, 108, buckets=_hot_buckets(12))
    _seed_due_item(items, uid)
    sender = _Sender()

    stats = await _pass(clock, users, items, sender, dry_run=True)

    assert sender.sent == []
    assert stats.dry_run is True
    assert stats.sent == 0
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 0


async def test_pass_sender_failure_is_not_counted_and_does_not_stop_the_pass(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    broken = _seed_user(users, session, clock, 201, buckets=_hot_buckets(12))
    ok = _seed_user(users, session, clock, 202, buckets=_hot_buckets(12))
    _seed_due_item(items, broken)
    _seed_due_item(items, ok)
    sender = _Sender(fail_for=frozenset({201}))

    stats = await _pass(clock, users, items, sender)

    assert [t for t, _ in sender.sent] == [202]  # 201 failed, 202 still sent
    failed = session.get(User, broken)
    assert failed is not None
    assert failed.proactive_count == 0  # failed send is not counted
    assert stats.sent == 1
    assert stats.skipped.get("send failed") == 1


async def test_on_demand_review_does_not_spend_the_proactive_budget(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    # A user reviewed everything on-demand (update_srs path — the review
    # session never calls record_proactive). Their proactive budget is
    # untouched; when a NEW card becomes due, proactive still fires.
    uid = _seed_user(users, session, clock, 301, buckets=_hot_buckets(12))
    first = _seed_due_item(items, uid, front="apple")
    from spacedbro.srs import Quality, SRSState, SRSStatus, advance

    item = items.get(uid, first)
    assert item is not None
    new_state = advance(
        SRSState(
            front=item.front,
            back=item.back,
            context=item.context,
            repetitions=item.repetitions,
            ease=item.ease,
            interval_minutes=item.interval_minutes,
            next_review_at=item.next_review_at,
            last_review_at=item.last_review_at,
            status=SRSStatus(item.status),
        ),
        Quality.GOOD,
        clock.utc_now(),
    )
    items.update_srs(
        uid,
        first,
        new_state.repetitions,
        new_state.ease,
        new_state.interval_minutes,
        new_state.next_review_at,
        new_state.last_review_at,
        new_state.status,
    )
    assert items.due_count(uid, clock.utc_now()) == 0  # reviewed
    sender = _Sender()

    # Pass 1: nothing due (it was reviewed on-demand), budget untouched.
    await _pass(clock, users, items, sender)
    assert sender.sent == []
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 0

    # A second card matures and becomes due -> proactive fires with the
    # FULL budget (on-demand never spent it).
    _seed_due_item(items, uid, front="bread")
    await _pass(clock, users, items, sender)
    assert [t for t, _ in sender.sent] == [301]
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 1


async def test_pass_nudge_reports_due_count_with_plural(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    uid = _seed_user(users, session, clock, 401, buckets=_hot_buckets(12))
    for i in range(3):
        _seed_due_item(items, uid, front=f"word{i}")
    sender = _Sender()

    await _pass(clock, users, items, sender)

    assert len(sender.sent) == 1
    assert "3" in sender.sent[0][1]
    assert render_nudge(3) != render_nudge(1)  # plural form differs


async def test_pass_stats_shape(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    _seed_user(users, session, clock, 501, buckets=_hot_buckets(12))  # no due items
    uid2 = _seed_user(users, session, clock, 502, buckets=[0] * 24)  # cold start, no due
    _seed_due_item(items, uid2)
    sender = _Sender()

    stats = await _pass(clock, users, items, sender)

    assert stats.checked == 2
    assert stats.sent == 1
    assert stats.dry_run is False
    assert stats.skipped.get("no due items") == 1


# --- The tick: real wiring of pass + repository, frozen clock -----------------


async def test_tick_runs_the_pass_with_fresh_session(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    uid = _seed_user(users, session, clock, 601, buckets=_hot_buckets(12))
    _seed_due_item(items, uid)
    sender = _Sender()
    factory = lambda: Session(session.bind, expire_on_commit=False)  # noqa: E731

    await _proactive_tick(clock, sender, factory, dry_run=False)

    assert [t for t, _ in sender.sent] == [601]
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 1


async def test_tick_dry_run_runs_pass_but_sends_nothing(
    session: Session,
    clock: FrozenClock,
    users: UserRepository,
    items: ItemRepository,
) -> None:
    uid = _seed_user(users, session, clock, 602, buckets=_hot_buckets(12))
    _seed_due_item(items, uid)
    sender = _Sender()
    factory = lambda: Session(session.bind, expire_on_commit=False)  # noqa: E731

    await _proactive_tick(clock, sender, factory, dry_run=True)

    assert sender.sent == []  # dry run: nothing sent, nothing counted
    user = session.get(User, uid)
    assert user is not None
    assert user.proactive_count == 0
