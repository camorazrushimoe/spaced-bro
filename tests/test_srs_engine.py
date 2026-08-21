"""Unit tests for the deterministic SRS engine (BON-28, design.md §6).

The engine is a pure function ``(state, quality, now) -> new_state``. A frozen
clock is simply a fixed UTC ``datetime`` passed in as ``now`` — no wall clock,
no Telegram, no LLM.

Easy interpretation (documented in ``srs/engine.py``): Easy shares Good's two
fixed first-review intervals (reps == 0 -> 1440, reps == 1 -> 4320) and only
diverges in the steady state (reps >= 2) with the ``*ease*1.3`` growth factor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spacedbro.srs import (
    MAX_INTERVAL_MINUTES,
    Quality,
    SRSState,
    SRSStatus,
    advance,
    boost,
    new_state,
)

# Frozen clock: a single fixed instant, injected as ``now`` everywhere.
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def make_state(**overrides) -> SRSState:
    """Build a fully-specified SRSState with sane defaults, then override."""
    base = dict(
        front="hello",
        back="привет",
        context=None,
        repetitions=0,
        ease=2.5,
        interval_minutes=20,
        next_review_at=NOW + timedelta(minutes=20),
        last_review_at=None,
        status=SRSStatus.LEARNING,
    )
    base.update(overrides)
    return SRSState(**base)


# --- New / Boost state ------------------------------------------------------

def test_new_state_fields() -> None:
    state = new_state("hello", "привет", None, NOW)

    assert state.repetitions == 0
    assert state.ease == 2.5
    assert state.interval_minutes == 20
    assert state.next_review_at == NOW + timedelta(minutes=20)
    assert state.status is SRSStatus.LEARNING
    assert state.last_review_at is None


def test_new_state_keeps_content() -> None:
    state = new_state("  Hello   World ", "def", "a photo caption", NOW)

    assert state.front == "  Hello   World "
    assert state.back == "def"
    assert state.context == "a photo caption"


def test_boost_resets_to_new_state_and_keeps_content() -> None:
    old = make_state(
        front="cat",
        back="кот",
        context="from a photo",
        repetitions=7,
        ease=2.1,
        interval_minutes=432_000,  # ~300 days, well past the cap
        next_review_at=NOW + timedelta(days=300),
        last_review_at=NOW - timedelta(days=300),
        status=SRSStatus.REVIEW,
    )

    boosted = boost(old, NOW)

    # Matches a fresh new_state at NOW ...
    expected = new_state(old.front, old.back, old.context, NOW)
    assert boosted == expected
    # ... and preserves the card content.
    assert (boosted.front, boosted.back, boosted.context) == (
        "cat",
        "кот",
        "from a photo",
    )
    assert boosted.repetitions == 0
    assert boosted.ease == 2.5
    assert boosted.interval_minutes == 20
    assert boosted.next_review_at == NOW + timedelta(minutes=20)
    assert boosted.status is SRSStatus.LEARNING


# --- Again ------------------------------------------------------------------

def test_again_resets_repetitions_and_interval() -> None:
    state = make_state(repetitions=3, ease=2.5, interval_minutes=10800, status=SRSStatus.REVIEW)

    result = advance(state, Quality.AGAIN, NOW)

    assert result.repetitions == 0
    assert result.interval_minutes == 10
    assert result.ease == pytest.approx(2.3)
    assert result.status is SRSStatus.LEARNING
    assert result.next_review_at == NOW + timedelta(minutes=10)
    assert result.last_review_at == NOW


def test_again_ease_floors_at_min() -> None:
    assert advance(make_state(ease=1.3), Quality.AGAIN, NOW).ease == pytest.approx(1.3)
    assert advance(make_state(ease=1.4), Quality.AGAIN, NOW).ease == pytest.approx(1.3)
    # 1.4 - 0.2 = 1.2, clamped up to the 1.3 floor.
    assert advance(make_state(ease=1.5), Quality.AGAIN, NOW).ease == pytest.approx(1.3)


# --- Hard -------------------------------------------------------------------

def test_hard_grows_interval_and_penalizes_ease() -> None:
    state = make_state(repetitions=1, ease=2.5, interval_minutes=100, status=SRSStatus.LEARNING)

    result = advance(state, Quality.HARD, NOW)

    assert result.interval_minutes == 120  # int(100 * 1.2)
    assert result.ease == pytest.approx(2.35)  # 2.5 - 0.15
    assert result.repetitions == 2
    # 120 < 1440, so a learning card stays learning.
    assert result.status is SRSStatus.LEARNING
    assert result.next_review_at == NOW + timedelta(minutes=120)


def test_hard_uses_floor_for_tiny_intervals() -> None:
    state = make_state(repetitions=0, ease=2.5, interval_minutes=5)

    result = advance(state, Quality.HARD, NOW)

    # max(10, int(5 * 1.2)) = max(10, 6) = 10
    assert result.interval_minutes == 10
    assert result.repetitions == 1


def test_hard_ease_floors_at_min() -> None:
    assert advance(make_state(ease=1.3), Quality.HARD, NOW).ease == pytest.approx(1.3)
    assert advance(make_state(ease=1.4), Quality.HARD, NOW).ease == pytest.approx(1.3)


def test_hard_keeps_review_when_interval_stays_large() -> None:
    state = make_state(repetitions=5, ease=2.5, interval_minutes=432_000, status=SRSStatus.REVIEW)

    result = advance(state, Quality.HARD, NOW)

    # 432000 * 1.2 = 518400 -> capped to MAX, still a review card.
    assert result.interval_minutes == MAX_INTERVAL_MINUTES
    assert result.status is SRSStatus.REVIEW


# --- Good -------------------------------------------------------------------

def test_good_first_success_sets_one_day() -> None:
    state = make_state(repetitions=0, ease=2.5, interval_minutes=20)

    result = advance(state, Quality.GOOD, NOW)

    assert result.interval_minutes == 1440
    assert result.repetitions == 1
    assert result.ease == pytest.approx(2.5)  # unchanged
    assert result.status is SRSStatus.REVIEW  # 1440 >= 1440
    assert result.next_review_at == NOW + timedelta(minutes=1440)


def test_good_second_sets_three_days() -> None:
    state = make_state(repetitions=1, ease=2.5, interval_minutes=1440, status=SRSStatus.REVIEW)

    result = advance(state, Quality.GOOD, NOW)

    assert result.interval_minutes == 4320
    assert result.repetitions == 2
    assert result.ease == pytest.approx(2.5)
    assert result.status is SRSStatus.REVIEW
    assert result.next_review_at == NOW + timedelta(minutes=4320)


def test_good_steady_multiplies_by_ease() -> None:
    state = make_state(repetitions=2, ease=2.5, interval_minutes=4320, status=SRSStatus.REVIEW)

    result = advance(state, Quality.GOOD, NOW)

    assert result.interval_minutes == int(4320 * 2.5)  # 10800
    assert result.repetitions == 3
    assert result.ease == pytest.approx(2.5)
    assert result.status is SRSStatus.REVIEW


# --- Easy -------------------------------------------------------------------

def test_easy_first_matches_good_but_raises_ease() -> None:
    state = make_state(repetitions=0, ease=2.5, interval_minutes=20)

    result = advance(state, Quality.EASY, NOW)

    assert result.interval_minutes == 1440  # same first-review interval as Good
    assert result.repetitions == 1
    assert result.ease == pytest.approx(2.65)  # 2.5 + 0.15
    assert result.status is SRSStatus.REVIEW


def test_easy_second_matches_good_but_raises_ease() -> None:
    state = make_state(repetitions=1, ease=2.5, interval_minutes=1440, status=SRSStatus.REVIEW)

    result = advance(state, Quality.EASY, NOW)

    assert result.interval_minutes == 4320
    assert result.repetitions == 2
    assert result.ease == pytest.approx(2.65)
    assert result.status is SRSStatus.REVIEW


def test_easy_steady_grows_faster_than_good() -> None:
    state = make_state(repetitions=2, ease=2.5, interval_minutes=4320, status=SRSStatus.REVIEW)

    good = advance(state, Quality.GOOD, NOW)
    easy = advance(state, Quality.EASY, NOW)

    assert easy.interval_minutes == int(4320 * 2.5 * 1.3)  # 14040
    assert easy.interval_minutes > good.interval_minutes  # 14040 > 10800
    assert easy.ease == pytest.approx(2.65)
    assert easy.repetitions == 3
    assert easy.status is SRSStatus.REVIEW


# --- Cap --------------------------------------------------------------------

def test_interval_is_capped_at_180_days() -> None:
    state = make_state(
        repetitions=2,
        ease=2.5,
        interval_minutes=MAX_INTERVAL_MINUTES,  # already at the cap
        status=SRSStatus.REVIEW,
    )

    result = advance(state, Quality.GOOD, NOW)

    # int(259200 * 2.5) = 648000, clamped back to the cap.
    assert result.interval_minutes == MAX_INTERVAL_MINUTES
    assert result.next_review_at == NOW + timedelta(minutes=MAX_INTERVAL_MINUTES)


def test_easy_interval_is_capped_at_180_days() -> None:
    state = make_state(
        repetitions=2,
        ease=2.5,
        interval_minutes=200_000,
        status=SRSStatus.REVIEW,
    )

    result = advance(state, Quality.EASY, NOW)

    # int(200000 * 2.5 * 1.3) = 650000, clamped to the cap.
    assert result.interval_minutes == MAX_INTERVAL_MINUTES


# --- Purity -----------------------------------------------------------------

def test_advance_is_pure_and_does_not_mutate_input() -> None:
    original = make_state(repetitions=1, ease=2.5, interval_minutes=1440, status=SRSStatus.REVIEW)
    snapshot = original  # frozen dataclass — compare field-by-field after

    result = advance(original, Quality.GOOD, NOW)

    assert result is not original
    # The input is untouched.
    assert original == snapshot
    assert original.repetitions == 1
    assert original.ease == 2.5
    assert original.interval_minutes == 1440
    assert original.status is SRSStatus.REVIEW


def test_status_promotes_to_review_at_one_day_threshold() -> None:
    # A steady-state Good whose multiplied interval lands >= 1 day becomes review.
    state = make_state(repetitions=2, ease=10.0, interval_minutes=150, status=SRSStatus.LEARNING)
    # int(150 * 10.0) = 1500 >= 1440 -> review.
    assert advance(state, Quality.GOOD, NOW).status is SRSStatus.REVIEW
