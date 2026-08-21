"""Deterministic SRS engine (BON-28).

Implements the exact, fixed mapping in ``openspec/changes/mvp-core/design.md``
§6 as a **pure function**: ``(state, quality, now) -> new_state``. There is no
wall-clock read, no I/O, no randomness, and no mutation of the input — so it
is fully unit-testable by injecting a fixed ``now`` (a "frozen clock").

All datetimes are timezone-aware UTC. Intervals are expressed in minutes
(``interval_minutes``) so sub-day scheduling is supported, per design §5.

Easy interpretation (documented here for the reviewers): the design's Easy row
reads *"same as Good but ``interval_minutes = int(interval_minutes * ease *
1.3)`` after the first two steps"*. The "first two steps" are Good's two fixed
first-review intervals (``repetitions == 0 -> 1440``, ``repetitions == 1 ->
4320``). Easy shares those two and only diverges in the steady state
(``repetitions >= 2``), where it grows by ``int(interval * ease * 1.3)`` — i.e.
faster than Good's ``int(interval * ease)``. See the srs-engine spec scenario
"Easy increases ease" ("interval grows faster than Good (per design formula)").
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

# --- Design §6 constants ----------------------------------------------------

#: Minimum ease value (ease never drops below this).
MIN_EASE = 1.3
#: Maximum interval, in minutes: 180 days.
MAX_INTERVAL_MINUTES = 259200
#: Minimum interval applied by Again/Hard, in minutes.
MIN_REVIEW_MINUTES = 10
#: Interval of a fresh / boosted item, in minutes.
NEW_INTERVAL_MINUTES = 20
#: Ease of a fresh / boosted item.
NEW_EASE = 2.5
#: Good's first-review (repetitions == 0) interval, in minutes (1 day).
ONE_DAY_MINUTES = 1440
#: Good's second-review (repetitions == 1) interval, in minutes (3 days).
THREE_DAYS_MINUTES = 4320
#: Threshold interval that promotes an item to the ``review`` status.
REVIEW_THRESHOLD_MINUTES = 1440
#: Ease penalty for Again.
AGAIN_EASE_PENALTY = 0.2
#: Ease penalty for Hard.
HARD_EASE_PENALTY = 0.15
#: Interval growth factor for Hard.
HARD_INTERVAL_FACTOR = 1.2
#: Ease bonus for Easy.
EASY_EASE_BONUS = 0.15
#: Steady-state interval growth factor for Easy (multiplied on top of ease).
EASY_STEADY_FACTOR = 1.3


class Quality(str, Enum):
    """Review quality grades, per design §6."""

    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


class SRSStatus(str, Enum):
    """SRS lifecycle status of a learning item."""

    LEARNING = "learning"
    REVIEW = "review"


@dataclass(frozen=True)
class SRSState:
    """SRS-related fields of a learning item.

    Card content (``front``/``back``/``context``) is carried here so the engine
    can return a complete next state and so ``boost`` can preserve it. The state
    is immutable (``frozen``) — the pure function returns a new instance.
    """

    front: str
    back: str
    context: Optional[str]
    repetitions: int
    ease: float
    interval_minutes: int
    next_review_at: datetime
    last_review_at: Optional[datetime]
    status: SRSStatus


def new_state(
    front: str,
    back: str,
    context: Optional[str],
    now: datetime,
) -> SRSState:
    """Build the New / Boost state at ``now`` (design §6 "New / Boost state")."""
    return SRSState(
        front=front,
        back=back,
        context=context,
        repetitions=0,
        ease=NEW_EASE,
        interval_minutes=NEW_INTERVAL_MINUTES,
        next_review_at=now + timedelta(minutes=NEW_INTERVAL_MINUTES),
        last_review_at=None,
        status=SRSStatus.LEARNING,
    )


def boost(state: SRSState, now: datetime) -> SRSState:
    """Reset an item to the New state, keeping ``front``/``back``/``context``.

    Design §6 "Boost": set state equal to the New / Boost state; preserve the
    card content.
    """
    return new_state(state.front, state.back, state.context, now)


def advance(state: SRSState, quality: Quality, now: datetime) -> SRSState:
    """Pure ``quality -> next state`` transition (design §6 quality table).

    Returns a new :class:`SRSState`; ``state`` is never mutated.
    """
    if quality is Quality.AGAIN:
        interval = MIN_REVIEW_MINUTES
        ease = max(MIN_EASE, state.ease - AGAIN_EASE_PENALTY)
        return replace(
            state,
            repetitions=0,
            interval_minutes=interval,
            ease=ease,
            next_review_at=now + timedelta(minutes=interval),
            last_review_at=now,
            status=SRSStatus.LEARNING,
        )

    if quality is Quality.HARD:
        raw = max(MIN_REVIEW_MINUTES, int(state.interval_minutes * HARD_INTERVAL_FACTOR))
        interval = min(MAX_INTERVAL_MINUTES, raw)
        ease = max(MIN_EASE, state.ease - HARD_EASE_PENALTY)
        # The Hard row specifies no status change, so the item keeps its status.
        return replace(
            state,
            interval_minutes=interval,
            ease=ease,
            repetitions=state.repetitions + 1,
            next_review_at=now + timedelta(minutes=interval),
            last_review_at=now,
        )

    if quality is Quality.GOOD:
        raw = _good_interval(state)
    elif quality is Quality.EASY:
        raw = _easy_interval(state)
    else:  # pragma: no cover - Quality is a closed enum
        raise ValueError(f"Unknown quality: {quality!r}")

    interval = min(MAX_INTERVAL_MINUTES, raw)
    ease = state.ease if quality is Quality.GOOD else state.ease + EASY_EASE_BONUS
    # Good and Easy: "status = review when interval >= 1440". Below the
    # threshold the item keeps its current status (the spec only mandates the
    # promotion, not a demotion).
    status = (
        SRSStatus.REVIEW
        if interval >= REVIEW_THRESHOLD_MINUTES
        else state.status
    )
    return replace(
        state,
        interval_minutes=interval,
        ease=ease,
        repetitions=state.repetitions + 1,
        next_review_at=now + timedelta(minutes=interval),
        last_review_at=now,
        status=status,
    )


def _fixed_interval(state: SRSState) -> Optional[int]:
    """The fixed interval for the first two reviews, shared by Good and Easy.

    Returns ``1440`` on the first review, ``4320`` on the second, and ``None``
    in the steady state (``repetitions >= 2``) so the caller applies its own
    growth formula. This is the design's "first two steps" that Good and Easy
    both share before diverging.
    """
    if state.repetitions == 0:
        return ONE_DAY_MINUTES
    if state.repetitions == 1:
        return THREE_DAYS_MINUTES
    return None


def _good_interval(state: SRSState) -> int:
    """Good's interval: fixed first two reviews, then ``int(interval * ease)``."""
    fixed = _fixed_interval(state)
    if fixed is not None:
        return fixed
    return int(state.interval_minutes * state.ease)


def _easy_interval(state: SRSState) -> int:
    """Easy's interval: share Good's first two reviews, then grow faster.

    Steady state (``repetitions >= 2``): ``int(interval * ease * 1.3)`` — 30%
    faster than Good (see module docstring for the interpretation).
    """
    fixed = _fixed_interval(state)
    if fixed is not None:
        return fixed
    return int(state.interval_minutes * state.ease * EASY_STEADY_FACTOR)
