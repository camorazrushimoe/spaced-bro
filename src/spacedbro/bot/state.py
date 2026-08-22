"""In-process user context state for the add flow (BON-31).

Telegram message updates are handled in a single long-polling process, so a
plain per-user dict is a safe, sufficient state container for the short-lived
conversation state of the add flow:

- the pending candidate list after a text/photo extraction;
- the pending ``back`` confirmation (front + generated back + context);
- the onboarding flag (whether the target-language question was answered).

All entries expire after a TTL so state never outlives the conversation.
This is deliberately **not** persistent: if the process restarts the user
simply re-sends the message (re-extraction is cheap and idempotent by
design — the card is only persisted on Save).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

#: Default state lifetime: 2 hours of inactivity.
DEFAULT_TTL_SECONDS = 7200


@dataclass(frozen=True, slots=True)
class Candidate:
    """One extraction result: a word/phrase to learn (+ optional context)."""

    front: str
    context: str | None = None


@dataclass(frozen=True, slots=True)
class Confirmation:
    """The pending confirm-back state shown to the user (design §5, step 4).

    ``key`` binds the confirmation to the buttons currently on screen
    (see ``spacedbro.bot.callbacks.new_key``): after a regenerate the
    store gets a fresh key, so stale [Save] buttons from an older
    confirmation cannot reach the newer one.
    """

    front: str
    back: str
    context: str | None = None
    key: str = ""


class ContextStore:
    """Per-Telegram-user, per-key state with TTL expiry.

    Safe for the single-event-loop process model (no locking needed).
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, *, time_fn: Any = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._time = time_fn
        self._entries: dict[tuple[int, str], tuple[float, Any]] = {}

    def _key(self, telegram_id: int, name: str) -> tuple[int, str]:
        return (telegram_id, name)

    def set(self, telegram_id: int, name: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires = self._time() + (ttl_seconds if ttl_seconds is not None else self._ttl)
        self._entries[self._key(telegram_id, name)] = (expires, value)

    def get(self, telegram_id: int, name: str) -> Any | None:
        entry = self._entries.get(self._key(telegram_id, name))
        if entry is None:
            return None
        expires, value = entry
        if self._time() >= expires:
            del self._entries[self._key(telegram_id, name)]
            return None
        return value

    def pop(self, telegram_id: int, name: str) -> Any | None:
        entry = self._entries.pop(self._key(telegram_id, name), None)
        if entry is None:
            return None
        expires, value = entry
        if self._time() >= expires:
            return None
        return value

    def has(self, telegram_id: int, name: str) -> bool:
        return self.get(telegram_id, name) is not None

    def clear_user(self, telegram_id: int) -> None:
        """Drop every key for one user (e.g. after a successful save)."""
        for key in [k for k in self._entries if k[0] == telegram_id]:
            del self._entries[key]


class CallbackLedger:
    """Processed-callback registry — the idempotency gate (design §1).

    Each actionable callback carries a unique ``callback_id`` (a nonce
    embedded in the callback data, see ``spacedbro.bot.callbacks``). The
    first presentation of an id is processed exactly once; every later
    presentation (double-tap, Telegram redelivery) is a no-op.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def first_seen(self, callback_id: str) -> bool:
        """True the first time ``callback_id`` is seen, False on replays."""
        if callback_id in self._seen:
            return False
        self._seen.add(callback_id)
        return True
