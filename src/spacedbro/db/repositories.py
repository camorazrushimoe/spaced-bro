"""Repository layer for users and learning items (BON-29, tasks.md §2).

Deep, small interfaces on top of the SQLite schema:

- :class:`UserRepository` — profile upsert (spec defaults), language and
  level updates, UTC activity timestamp + histogram, and the proactive
  daily counters (design §4, §8).
- :class:`ItemRepository` — card persistence with the New SRS state,
  front normalization, duplicate check, boost, due query and listing
  (design §5–§7, learning-items spec).

Timezone contract: every datetime crossing the boundary must be **aware
UTC**. The :class:`~spacedbro.db.types.UTCDatetime` column type normalizes
to UTC on write and re-attaches UTC on read (rejecting naive input with a
clear error), so callers and downstream engines (SRS, scheduler) always see
aware UTC — even on plain ORM reads outside these repositories.

The repositories own commit semantics: every mutating method commits
atomically, so each one maps to one database transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spacedbro.clock import Clock
from spacedbro.db.models import (
    NEW_EASE,
    NEW_INTERVAL_MINUTES,
    ItemStatus,
    LearningItem,
    User,
)

#: 24-bucket UTC activity histogram (index == UTC hour).
ACTIVITY_BUCKETS = 24


class ItemNotFoundError(LookupError):
    """Raised when an item id does not belong to the given user."""


def normalize_front(front: str) -> str:
    """Exact front normalization from learning-items/spec.md.

    ``" ".join(front.casefold().split())`` — case-insensitive and
    whitespace-insensitive, so ``Hello`` and `` hello `` are the same item.
    """
    return " ".join(front.casefold().split())


def _require_aware(dt: datetime) -> datetime:
    """Reject naive datetimes at the repository boundary.

    Fails fast with a clear message instead of letting a naive value reach
    the column type (which would still reject it, but deeper in the stack).
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(
            "All timestamps must be timezone-aware UTC; got a naive datetime"
        )
    return dt.astimezone(timezone.utc)


class UserRepository:
    """Profile CRUD, activity tracking, and proactive counters per user."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self.session = session
        self._clock = clock

    # --- Lookup / creation ---------------------------------------------------

    def get(self, user_id: int) -> Optional[User]:
        """The profile for ``user_id``, or ``None``."""
        return self.session.get(User, user_id)

    def get_by_telegram_id(self, telegram_id: int) -> Optional[int]:
        """Internal user id for a Telegram user, or ``None``."""
        stmt = select(User.id).where(User.telegram_id == telegram_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def profile(self, telegram_id: int) -> Optional[User]:
        """The full profile row for a Telegram user, or ``None``."""
        return self.session.scalar(select(User).where(User.telegram_id == telegram_id))

    def get_or_create(self, telegram_id: int, now: Optional[datetime] = None) -> int:
        """Return the user id for ``telegram_id``, creating the profile on
        first interaction.

        user-memory spec "First interaction": new profiles get
        ``native_lang='ru'``, ``target_lang='en'``, zeroed activity and
        proactive state, and UTC ``created_at`` / ``last_active_at``.
        """
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        user = self.session.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )
        if user is None:
            user = User(
                telegram_id=telegram_id,
                created_at=instant,
                last_active_at=instant,
                activity_hours_utc=[0] * ACTIVITY_BUCKETS,
                proactive_count=0,
                proactive_count_date=None,
            )
            self.session.add(user)
            self.session.commit()
        return user.id

    # --- Profile updates -----------------------------------------------------

    def set_target_lang(self, user_id: int, target_lang: str) -> None:
        """Update the single target language.

        The double-confirmation protocol (propose → confirm → confirm) lives
        in the bot layer; this method is only ever called once both
        confirmations are complete (user-memory spec "Single target
        language").
        """
        user = self._get_or_raise(user_id)
        user.target_lang = target_lang
        self.session.commit()

    def set_native_lang(self, user_id: int, native_lang: str) -> None:
        user = self._get_or_raise(user_id)
        user.native_lang = native_lang
        self.session.commit()

    def set_ui_lang(self, user_id: int, ui_lang: Optional[str]) -> None:
        user = self._get_or_raise(user_id)
        user.ui_lang = ui_lang
        self.session.commit()

    def set_level_estimate(self, user_id: int, level: str) -> None:
        user = self._get_or_raise(user_id)
        user.level_estimate = level
        self.session.commit()

    def mark_onboarding_asked(self, user_id: int) -> None:
        """Remember that the onboarding target-language question was answered.

        telegram-bot spec "Start command": the bot asks the target language
        **once**; this flag is what makes later ``/start`` a plain welcome.
        """
        user = self._get_or_raise(user_id)
        user.onboarding_asked = True
        self.session.commit()

    def update_last_active(self, user_id: int, now: Optional[datetime] = None) -> None:
        """Refresh ``last_active_at`` (UTC). Creation time is untouched."""
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        user = self._get_or_raise(user_id)
        user.last_active_at = instant
        self.session.commit()

    # --- Activity (UTC histogram, design §4 / §8) ----------------------------

    def touch_activity(self, user_id: int, now: Optional[datetime] = None) -> None:
        """Count one user interaction in the UTC-hour bucket of ``now``.

        Feeds the scheduler's activity heuristics: ">=3 distinct UTC hours
        in the last 7 days" is derived from this histogram.
        """
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        user = self._get_or_raise(user_id)
        buckets = list(user.activity_hours_utc or [0] * ACTIVITY_BUCKETS)
        while len(buckets) < ACTIVITY_BUCKETS:
            buckets.append(0)
        buckets[instant.hour] += 1
        user.activity_hours_utc = buckets
        user.last_active_at = instant
        self.session.commit()

    # --- Proactive daily counters (design §8) --------------------------------

    def record_proactive(self, user_id: int, now: Optional[datetime] = None) -> int:
        """Count one proactive message sent. Returns the day's new count.

        The counter resets at UTC midnight (design §8 "Day = calendar UTC
        date"): when ``now`` falls on a new UTC date the count restarts at 1.
        On-demand reviews do NOT call this (spec §7).
        """
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        user = self._get_or_raise(user_id)
        if user.proactive_count_date != instant.date():
            user.proactive_count = 0
            user.proactive_count_date = instant.date()
        user.proactive_count += 1
        self.session.commit()
        return user.proactive_count

    def proactive_under_cap(
        self, user_id: int, cap: int, now: Optional[datetime] = None
    ) -> bool:
        """Whether the user may receive another proactive message today.

        Unknown users and users on a fresh UTC date are under any cap.
        """
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        user = self.session.get(User, user_id)
        if user is None:
            return True
        if user.proactive_count_date != instant.date():
            return True
        return user.proactive_count < cap

    # --- Helpers ---------------------------------------------------------------

    def _get_or_raise(self, user_id: int) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise LookupError(f"User {user_id} not found")
        return user


class ItemRepository:
    """Learning-item persistence: save, dedup, boost, due queue (design §5–§7)."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self.session = session
        self._clock = clock

    # --- Save / dedup ----------------------------------------------------------

    def save(
        self,
        user_id: int,
        front: str,
        back: str,
        context: Optional[str] = None,
        next_review_at: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> LearningItem:
        """Persist a card with the New SRS state (design §6 "New state").

        ``front`` is stored verbatim; ``normalized_front`` is the dedup
        key. Raises ``sqlalchemy.exc.IntegrityError`` when the normalized
        front already exists for this user — callers are expected to check
        :meth:`is_duplicate` first (the bot offers Boost in that case).
        """
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        due_at = _require_aware(
            next_review_at
            if next_review_at is not None
            else instant + timedelta(minutes=NEW_INTERVAL_MINUTES)
        )
        item = LearningItem(
            user_id=user_id,
            front=front,
            normalized_front=normalize_front(front),
            back=back,
            context=context,
            ease=NEW_EASE,
            interval_minutes=NEW_INTERVAL_MINUTES,
            repetitions=0,
            next_review_at=due_at,
            last_review_at=None,
            status=ItemStatus.LEARNING.value,
            created_at=instant,
        )
        self.session.add(item)
        self.session.commit()
        return item

    def find_by_front(self, user_id: int, front: str) -> Optional[int]:
        """Item id whose normalized front matches ``front``, or ``None``.

        learning-items spec "Front normalization": case- and
        whitespace-insensitive, per user.
        """
        stmt = select(LearningItem.id).where(
            LearningItem.user_id == user_id,
            LearningItem.normalized_front == normalize_front(front),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def is_duplicate(self, user_id: int, front: str) -> bool:
        """True when ``front`` already exists (normalized) for this user."""
        return self.find_by_front(user_id, front) is not None

    # --- SRS updates -----------------------------------------------------------

    def get(self, user_id: int, item_id: int) -> Optional[LearningItem]:
        """The item row for ``item_id`` if it belongs to ``user_id``.

        Read-only seam for handlers that need one card's current SRS
        state (the review session feeds it to the pure engine).
        """
        item = self.session.get(LearningItem, item_id)
        if item is None or item.user_id != user_id:
            return None
        return item

    def update_srs(
        self,
        user_id: int,
        item_id: int,
        repetitions: int,
        ease: float,
        interval_minutes: int,
        next_review_at: datetime,
        last_review_at: Optional[datetime],
        status: ItemStatus | str,
    ) -> LearningItem:
        """Persist the result of the SRS engine for one review.

        The repository stores exactly what the pure engine
        (``spacedbro.srs.advance``) returned — no scheduling logic of its
        own (seam: engine is pure, this layer only persists).
        """
        item = self._get_or_raise(user_id, item_id)
        item.repetitions = repetitions
        item.ease = ease
        item.interval_minutes = interval_minutes
        item.next_review_at = _require_aware(next_review_at)
        item.last_review_at = (
            _require_aware(last_review_at) if last_review_at is not None else None
        )
        item.status = status.value if isinstance(status, ItemStatus) else status
        self.session.commit()
        return item

    def boost(
        self, user_id: int, item_id: int, now: Optional[datetime] = None
    ) -> LearningItem:
        """Reset an item to the New state, keeping front/back/context.

        design §6 "Boost" + learning-items spec "Duplicates and boost": a
        duplicate does not create a second row — it re-queues the existing
        one.
        """
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        item = self._get_or_raise(user_id, item_id)
        item.repetitions = 0
        item.ease = NEW_EASE
        item.interval_minutes = NEW_INTERVAL_MINUTES
        item.next_review_at = instant + timedelta(minutes=NEW_INTERVAL_MINUTES)
        item.last_review_at = None
        item.status = ItemStatus.LEARNING.value
        self.session.commit()
        return item

    # --- Due queue (design §7) ---------------------------------------------------

    def due(self, user_id: int, now: Optional[datetime] = None) -> list[LearningItem]:
        """Items with ``next_review_at <= now_utc``, ascending by due time.

        learning-items spec "Due query". Includes the boundary
        (``== now`` is due).
        """
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        stmt = (
            select(LearningItem)
            .where(
                LearningItem.user_id == user_id,
                LearningItem.next_review_at <= instant,
            )
            .order_by(LearningItem.next_review_at.asc(), LearningItem.id.asc())
        )
        return list(self.session.execute(stmt).scalars())

    def due_count(self, user_id: int, now: Optional[datetime] = None) -> int:
        """How many items are due (design §7: "bot reports how many due")."""
        instant = _require_aware(now if now is not None else self._clock.utc_now())
        stmt = (
            select(func.count())
            .select_from(LearningItem)
            .where(
                LearningItem.user_id == user_id,
                LearningItem.next_review_at <= instant,
            )
        )
        return int(self.session.execute(stmt).scalar_one())

    def list(self, user_id: int) -> list[LearningItem]:
        """All of a user's items, oldest first."""
        stmt = (
            select(LearningItem)
            .where(LearningItem.user_id == user_id)
            .order_by(LearningItem.created_at.asc(), LearningItem.id.asc())
        )
        return list(self.session.execute(stmt).scalars())

    # --- Helpers ------------------------------------------------------------------

    def _get_or_raise(self, user_id: int, item_id: int) -> LearningItem:
        item = self.session.get(LearningItem, item_id)
        if item is None or item.user_id != user_id:
            raise ItemNotFoundError(f"Item {item_id} not found for user {user_id}")
        return item
