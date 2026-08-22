"""ORM models for the SQLite data model (BON-29).

Schema per ``openspec/changes/mvp-core/design.md`` "Data model (sketch)":

- ``users`` — one profile per Telegram user: language settings, level
  estimate, UTC activity fields (timestamps, 24-bucket histogram), and the
  proactive daily counters.
- ``learning_items`` — per-user flashcards with SRS state; unique per
  (``user_id``, ``normalized_front``) per
  ``openspec/changes/mvp-core/specs/learning-items/spec.md``.

All datetimes are stored as UTC. SQLite's ``DATETIME`` type does not carry
timezone information, so the repository layer (``spacedbro.db.repositories``)
is responsible for normalizing every timestamp to aware UTC before it is
persisted and for re-attaching UTC on read.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from spacedbro.db.base import Base
from spacedbro.db.types import UTCDatetime
from spacedbro.srs import NEW_EASE, NEW_INTERVAL_MINUTES

#: user-memory spec: profile created on first interaction gets these.
DEFAULT_NATIVE_LANG = "ru"
#: user-memory spec: only one target language; English by default.
DEFAULT_TARGET_LANG = "en"


class UserLevel(str, Enum):
    """Heuristic proficiency level (design §4)."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class ItemStatus(str, Enum):
    """SRS lifecycle status of a learning item (stored as a plain string)."""

    LEARNING = "learning"
    REVIEW = "review"


# --- Design §6 "New / Boost state" constants ----------------------------------
# The SRS engine (ticket BON-28, ``spacedbro.srs``) owns the full
# quality→state mapping and the New / Boost state values; the persistence
# layer imports them from there so the two layers can never drift apart.


class User(Base):
    """Per-Telegram-user profile (design §4 "User profile")."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        Integer, unique=True, nullable=False, index=True
    )
    native_lang: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=DEFAULT_NATIVE_LANG,
        server_default=DEFAULT_NATIVE_LANG,
    )
    target_lang: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=DEFAULT_TARGET_LANG,
        server_default=DEFAULT_TARGET_LANG,
    )
    #: UI/detected language; null until heuristics set one (default UI English).
    ui_lang: Mapped[str | None] = mapped_column(String(8), nullable=True)
    level_estimate: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=UserLevel.BEGINNER.value,
        server_default=UserLevel.BEGINNER.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDatetime, nullable=False
    )
    last_active_at: Mapped[datetime] = mapped_column(
        UTCDatetime, nullable=False
    )
    #: 24-bucket UTC activity histogram: index == UTC hour, value == count.
    activity_hours_utc: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: [0] * 24
    )
    #: Proactive messages sent on ``proactive_count_date`` (UTC date).
    proactive_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    proactive_count_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    #: Whether the onboarding target-language question has been answered
    #: (telegram-bot "Start command": ask target language **once**).
    onboarding_asked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("0")
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User telegram_id={self.telegram_id}>"


class LearningItem(Base):
    """A single flashcard with SRS state (design §5).

    New / Boost state defaults (design §6) live on the columns so any
    persistence path that inserts a fresh card gets a correct SRS state:
    ``ease=2.5``, ``interval_minutes=20``, ``repetitions=0``,
    ``status='learning'``.
    """

    __tablename__ = "learning_items"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "normalized_front",
            name="uq_learning_items_user_normalized_front",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Original user-facing card text (exactly as provided).
    front: Mapped[str] = mapped_column(Text, nullable=False)
    #: ``" ".join(front.casefold().split())`` — the deduplication key.
    normalized_front: Mapped[str] = mapped_column(Text, nullable=False)
    #: Short translation/definition in the user's native language.
    back: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    ease: Mapped[float] = mapped_column(
        Float, nullable=False, default=NEW_EASE, server_default=sa_text("2.5")
    )
    interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=NEW_INTERVAL_MINUTES,
        server_default=sa_text("20"),
    )
    repetitions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    next_review_at: Mapped[datetime] = mapped_column(
        UTCDatetime, nullable=False
    )
    last_review_at: Mapped[datetime | None] = mapped_column(
        UTCDatetime, nullable=True
    )
    #: 'learning' | 'review' (see :class:`ItemStatus`).
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ItemStatus.LEARNING.value,
        server_default=ItemStatus.LEARNING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDatetime, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LearningItem id={self.id} user_id={self.user_id} front={self.front!r}>"
