"""Repository layer tests (BON-29, tasks.md §2 "Repositories").

Covers, per the ticket's acceptance criteria and the two capability specs:

- users repo: profile upsert with spec defaults (first interaction creates
  ``native_lang='ru'``, ``target_lang='en'``), single target language update,
  activity histogram (UTC buckets), proactive counters with UTC-date rollover.
- items repo: save with New SRS state, front normalization, duplicate check
  by (user_id, normalized_front) — case/space-insensitive, boost (SRS reset,
  content preserved, no second row), due query ``next_review_at <= now_utc``
  ordered ascending.
- All timestamps are timezone-aware UTC on read.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from spacedbro.db.base import Base
from spacedbro.db.models import (
    DEFAULT_NATIVE_LANG,
    DEFAULT_TARGET_LANG,
    NEW_EASE,
    NEW_INTERVAL_MINUTES,
    ItemStatus,
    LearningItem,
    User,
    UserLevel,
)
from spacedbro.db.repositories import (
    ItemNotFoundError,
    ItemRepository,
    UserRepository,
    normalize_front,
)

# --- Frozen clock (BON-27 injectable clock; tests never use the wall clock) ---

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def utc_now(self) -> datetime:
        return self._now


@pytest.fixture
def session() -> Session:
    """In-memory SQLite session with the full schema (migration-built DDL
    lives in the migration; here we build from the shared metadata)."""
    from spacedbro.db.engine import create_db_engine

    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


@pytest.fixture
def clock() -> _FrozenClock:
    return _FrozenClock(NOW)


@pytest.fixture
def users(session: Session, clock: _FrozenClock) -> UserRepository:
    return UserRepository(session, clock)


@pytest.fixture
def items(session: Session, clock: _FrozenClock) -> ItemRepository:
    return ItemRepository(session, clock)


@pytest.fixture
def user(users: UserRepository) -> int:
    return users.get_or_create(12345)


def _item_id_by_front(session: Session, user_id: int, normalized: str) -> int:
    row = session.execute(
        "SELECT id FROM learning_items WHERE user_id = :u AND normalized_front = :n",
        {"u": user_id, "n": normalized},
    ).one()
    return int(row[0])


# --- Front normalization (learning-items spec "Front normalization") ---------

def test_normalize_front_exact_rule() -> None:
    assert normalize_front("Hello") == "hello"
    assert normalize_front("  Hello   World ") == "hello world"
    assert normalize_front("HELLO\n\tworld") == "hello world"
    assert normalize_front("Животное  🐶") == "животное 🐶"
    assert normalize_front("   ") == ""


def test_normalize_matches_spec_formula() -> None:
    # The spec pins the exact formula; guard against drift.
    assert normalize_front("MiXeD  case") == " ".join("MiXeD  case".casefold().split())


# --- Users repository --------------------------------------------------------


def test_get_or_create_first_interaction_defaults(
    session: Session, users: UserRepository
) -> None:
    """user-memory spec "First interaction": defaults ru/en on creation."""
    uid = users.get_or_create(999)

    profile = users.get(uid)
    assert profile is not None
    assert profile.telegram_id == 999
    assert profile.native_lang == "ru"
    assert profile.target_lang == "en"
    assert profile.ui_lang is None
    assert profile.level_estimate == UserLevel.BEGINNER
    assert profile.proactive_count == 0
    assert profile.proactive_count_date is None
    assert profile.activity_hours_utc == [0] * 24
    assert profile.created_at == NOW
    assert profile.last_active_at == NOW


def test_get_or_create_idempotent(session: Session, users: UserRepository) -> None:
    first = users.get_or_create(42)
    users.update_last_active(first)
    second = users.get_or_create(42)

    assert first == second
    with session.execute(text("SELECT COUNT(*) FROM users")) as r:
        assert r.scalar() == 1


def test_get_missing_user_returns_none(session: Session, users: UserRepository) -> None:
    assert users.get(999) is None
    uid = users.get_or_create(2)
    assert users.get(uid) is not None
    assert users.get(999) is None


def test_get_by_telegram_id(session: Session, users: UserRepository) -> None:
    uid = users.get_or_create(777)
    assert users.get_by_telegram_id(777) == uid
    assert users.get_by_telegram_id(778) is None


def test_set_target_lang(session: Session, users: UserRepository) -> None:
    uid = users.get_or_create(1)
    users.set_target_lang(uid, "de")
    assert users.get(uid).target_lang == "de"


def test_set_native_lang_and_ui_lang(
    session: Session, users: UserRepository
) -> None:
    uid = users.get_or_create(1)
    users.set_native_lang(uid, "en")
    users.set_ui_lang(uid, "en")
    profile = users.get(uid)
    assert profile.native_lang == "en"
    assert profile.ui_lang == "en"
    # target_lang is independent — only the (double-confirmed) target update
    # changes it.
    assert profile.target_lang == "en"


def test_update_last_active(session: Session, users: UserRepository) -> None:
    uid = users.get_or_create(1)
    later = NOW + timedelta(hours=5)
    users.update_last_active(uid, now=later)

    profile = users.get(uid)
    assert profile.last_active_at == later
    assert profile.created_at == NOW  # creation time is not clobbered


def test_touch_activity_bucket_utc(session: Session, users: UserRepository) -> None:
    uid = users.get_or_create(1)
    users.touch_activity(uid)  # NOW is 12:00 UTC

    assert users.get(uid).activity_hours_utc[12] == 1
    assert sum(users.get(uid).activity_hours_utc) == 1
    assert len(users.get(uid).activity_hours_utc) == 24


def test_touch_activity_bucket_for_other_utc_hour(
    session: Session, users: UserRepository
) -> None:
    uid = users.get_or_create(1)
    users.touch_activity(uid, now=NOW - timedelta(days=1, hours=2))  # 10:00 UTC

    bucket = users.get(uid).activity_hours_utc[10]
    assert bucket == 1
    # Different bucket than NOW's 12:00
    assert users.get(uid).activity_hours_utc[12] == 0


def test_touch_activity_counts_distinct_hours(
    session: Session, users: UserRepository
) -> None:
    """Histogram holds per-event UTC-hour counts (design §4 "24-bucket
    counts"); distinct-hour heuristics derive from non-zero buckets."""
    uid = users.get_or_create(1)
    users.touch_activity(uid)  # hour 12
    users.touch_activity(uid, now=NOW - timedelta(hours=1))  # hour 11
    users.touch_activity(uid, now=NOW - timedelta(hours=1))  # hour 11 again

    buckets = users.get(uid).activity_hours_utc
    assert buckets[12] == 1
    assert buckets[11] == 2
    assert sum(buckets) == 3
    assert sum(1 for b in buckets if b > 0) == 2  # distinct active hours


def test_record_proactive_rollover_on_new_utc_date(
    session: Session, users: UserRepository
) -> None:
    uid = users.get_or_create(1)
    users.record_proactive(uid)
    assert users.get(uid).proactive_count == 1
    assert users.get(uid).proactive_count_date == NOW.date()

    same_day = NOW + timedelta(hours=3)
    users.record_proactive(uid, now=same_day)
    assert users.get(uid).proactive_count == 2

    next_day = NOW + timedelta(days=1, hours=-1)  # UTC date rolled over
    users.record_proactive(uid, now=next_day)
    assert users.get(uid).proactive_count == 1
    assert users.get(uid).proactive_count_date == next_day.date()


def test_proactive_under_cap(session: Session, users: UserRepository) -> None:
    uid = users.get_or_create(1)
    assert users.proactive_under_cap(uid, cap=3) is True
    users.record_proactive(uid)
    users.record_proactive(uid)
    assert users.proactive_under_cap(uid, cap=2) is False
    assert users.proactive_under_cap(uid, cap=3) is True
    # Unknown user: under cap (nothing sent yet).
    assert users.proactive_under_cap(uid + 1, cap=1) is True


# --- Items repository --------------------------------------------------------


def test_save_new_item_persists_new_srs_state(
    session: Session, users: UserRepository, items: ItemRepository, user: int
) -> None:
    """Design §6 New state: reps=0, ease=2.5, interval=20, due now+20min."""
    saved = items.save(user, front="Hello", back="привет", context=None)

    assert saved.id is not None
    assert saved.user_id == user
    assert saved.front == "Hello"
    assert saved.normalized_front == "hello"
    assert saved.back == "привет"
    assert saved.context is None
    assert saved.repetitions == 0
    assert saved.ease == NEW_EASE
    assert saved.interval_minutes == NEW_INTERVAL_MINUTES
    assert saved.next_review_at == NOW + timedelta(minutes=NEW_INTERVAL_MINUTES)
    assert saved.last_review_at is None
    assert saved.status == ItemStatus.LEARNING.value
    assert saved.created_at == NOW


def test_save_preserves_original_front_text(session: Session, items: ItemRepository, user: int) -> None:
    saved = items.save(user, front="  Hello   World ", back="привет мир")
    assert saved.front == "  Hello   World "
    assert saved.normalized_front == "hello world"


def test_save_stores_aware_utc_timestamps(
    session: Session, users: UserRepository, items: ItemRepository, user: int
) -> None:
    saved = items.save(user, front="cat", back="кот")
    assert saved.created_at.tzinfo is not None
    assert saved.created_at == NOW

    # Round-trip: a fresh session must hand back aware UTC, never naive.
    engine = items.session.get_bind()
    with Session(engine, expire_on_commit=False) as fresh:
        reloaded = fresh.get(LearningItem, saved.id)
        assert reloaded is not None
        assert reloaded.created_at.tzinfo is not None
        assert reloaded.created_at == NOW
        assert reloaded.next_review_at.tzinfo is not None
        assert reloaded.next_review_at == NOW + timedelta(minutes=20)


def test_save_rejects_naive_datetime(
    session: Session, users: UserRepository, items: ItemRepository, user: int
) -> None:
    """All timestamps are UTC: naive datetimes are rejected at the boundary."""
    with pytest.raises(ValueError, match="aware UTC"):
        items.save(
            user,
            front="cat",
            back="кот",
            now=datetime(2026, 8, 21, 12, 0, 0),  # naive
        )


def test_find_by_front_case_and_space_insensitive(
    session: Session, users: UserRepository, items: ItemRepository, user: int
) -> None:
    first = items.save(user, front="Hello", back="привет")
    # Spec scenario: adding ` hello ` is a duplicate of `Hello`.
    assert items.find_by_front(user, " hello ") is not None
    assert items.find_by_front(user, "HELLO") is not None
    assert items.find_by_front(user, "Hello World") is None
    assert items.find_by_front(user, "hello") == first.id


def test_duplicate_check(session: Session, items: ItemRepository, user: int) -> None:
    items.save(user, front="Hello", back="привет")
    assert items.is_duplicate(user, "hello") is True
    assert items.is_duplicate(user, "  HE LLO ") is False


def test_find_by_front_other_user_isolated(
    session: Session, users: UserRepository, items: ItemRepository
) -> None:
    """No cross-user sharing (user-memory spec "Privacy")."""
    user_a = users.get_or_create(111)
    items.save(user_a, front="Hello", back="привет")
    assert items.find_by_front(user_a + 1, "hello") is None
    assert items.is_duplicate(user_a + 1, "hello") is False
    assert items.find_by_front(user_a, "hello") is not None


def test_save_duplicate_enforced_by_unique_constraint(
    session: Session, items: ItemRepository, user: int
) -> None:
    items.save(user, front="Hello", back="привет")
    with pytest.raises(IntegrityError):
        items.save(user, front="HELLO", back="hello again")


def test_update_srs_fields(session: Session, items: ItemRepository, user: int) -> None:
    saved = items.save(user, front="dog", back="пёс")
    later = NOW + timedelta(minutes=20)
    updated = items.update_srs(
        user,
        saved.id,
        repetitions=1,
        ease=2.35,
        interval_minutes=1440,
        next_review_at=later + timedelta(minutes=1440),
        last_review_at=later,
        status=ItemStatus.REVIEW,
    )

    assert updated.repetitions == 1
    assert updated.ease == 2.35
    assert updated.interval_minutes == 1440
    assert updated.next_review_at == later + timedelta(minutes=1440)
    assert updated.last_review_at == later
    assert updated.status == ItemStatus.REVIEW.value


def test_update_srs_unknown_item_raises(
    session: Session, users: UserRepository, items: ItemRepository, user: int
) -> None:
    with pytest.raises(ItemNotFoundError):
        items.update_srs(
            user,
            999,
            repetitions=1,
            ease=2.5,
            interval_minutes=10,
            next_review_at=NOW + timedelta(minutes=10),
            last_review_at=NOW,
            status=ItemStatus.LEARNING,
        )


def test_boost_resets_srs_and_preserves_content(
    session: Session, items: ItemRepository, user: int
) -> None:
    saved = items.save(user, front="cat", back="кот", context="on the table")
    # Simulate an item that has aged to a long interval.
    items.update_srs(
        user,
        saved.id,
        repetitions=5,
        ease=2.8,
        interval_minutes=20000,
        next_review_at=NOW + timedelta(days=14),
        last_review_at=NOW - timedelta(days=10),
        status=ItemStatus.REVIEW,
    )

    boosted = items.boost(user, saved.id)

    assert boosted.repetitions == 0
    assert boosted.ease == NEW_EASE
    assert boosted.interval_minutes == NEW_INTERVAL_MINUTES
    assert boosted.next_review_at == NOW + timedelta(minutes=NEW_INTERVAL_MINUTES)
    assert boosted.status == ItemStatus.LEARNING.value
    assert boosted.last_review_at is None
    # Content preserved (design §6 Boost).
    assert boosted.front == "cat"
    assert boosted.back == "кот"
    assert boosted.context == "on the table"
    assert boosted.created_at == NOW  # not a new row


def test_boost_keeps_single_row(session: Session, items: ItemRepository, user: int) -> None:
    items.save(user, front="cat", back="кот")
    items.boost(user, items.find_by_front(user, "cat"))

    with session.execute(
        text("SELECT COUNT(*) FROM learning_items WHERE user_id = :u"), {"u": user}
    ) as r:
        assert r.scalar() == 1


def test_boost_unknown_item_raises(
    session: Session, users: UserRepository, items: ItemRepository, user: int
) -> None:
    with pytest.raises(ItemNotFoundError):
        items.boost(user, 4242)


def test_due_query_filters_orders_and_scopes(
    session: Session, users: UserRepository, items: ItemRepository
) -> None:
    user_100 = users.get_or_create(100)
    due_first = items.save(user_100, front="a", back="1", now=NOW - timedelta(hours=1), next_review_at=NOW - timedelta(hours=1))
    due_second = items.save(user_100, front="b", back="2", now=NOW, next_review_at=NOW)
    not_due = items.save(user_100, front="c", back="3", now=NOW, next_review_at=NOW + timedelta(minutes=1))
    other_user_id = users.get_or_create(667)
    other_user_due = items.save(other_user_id, front="d", back="4", now=NOW - timedelta(hours=2), next_review_at=NOW - timedelta(hours=2))

    due = items.due(user_100, NOW)

    assert [i.id for i in due] == [due_first.id, due_second.id]
    # Boundary: exactly now is due. Ordering: ascending by next_review_at.
    assert due[0].front == "a"
    assert due[1].front == "b"
    assert not_due.id not in [i.id for i in due]
    assert other_user_due.id not in [i.id for i in due]


def test_due_count(session: Session, items: ItemRepository, user: int) -> None:
    items.save(user, front="a", back="1", next_review_at=NOW - timedelta(hours=1))
    items.save(user, front="b", back="2")  # +20 min: not yet due
    assert items.due_count(user, NOW) == 1
    assert items.due_count(user, NOW + timedelta(hours=1)) == 2
    assert items.due_count(999, NOW) == 0


def test_due_empty_for_fresh_user(session: Session, items: ItemRepository, user: int) -> None:
    assert items.due(user, NOW) == []
    assert items.due_count(user, NOW) == 0


def test_list_items(session: Session, items: ItemRepository, user: int) -> None:
    items.save(user, front="a", back="1")
    items.save(user, front="b", back="2")
    assert len(items.list(user)) == 2
    assert len(items.list(4242)) == 0
