"""Callback data protocol and idempotency ids (BON-31, design §1).

Every actionable inline button carries a **unique** ``callback_id`` —
a short random nonce generated when the button is sent. The callback data
is a single string ``action:payload:callback_id``:

- ``action``    — one of the ``ACTION_*`` constants below;
- ``payload``   — action data: a short **opaque reference** (candidate
  token, confirmation token, ``item_id``, language code) or ``-`` when
  empty. Card content (front/back) is **never** embedded in the payload —
  it lives in the in-process store and is looked up by reference, which
  keeps every callback well under Telegram's 64-byte limit regardless of
  word length;
- ``callback_id`` — unique per button instance. Processing the same id
  twice is a no-op (see :class:`spacedbro.bot.state.CallbackLedger`),
  protecting against double-taps and Telegram redelivery.
"""

from __future__ import annotations

import uuid
from typing import Optional

# --- Actions (BON-31: the add flow; BON-32: the review session) -----------------

ACTION_ADD = "add"
ACTION_SAVE = "save"
ACTION_REGEN = "regen"
ACTION_SKIP = "skip"
ACTION_BOOST = "boost"
ACTION_LANG = "lang"
#: Review session (design §7): show the answer of the card on screen
#: (payload: ``item_id``).
ACTION_SHOW = "show"
#: Review session: quality rating (payload: ``quality:item_id``, quality
#: one of again/hard/good/easy — colons in the payload are fine, the
#: parser takes the first segment as action and the last as callback id).
ACTION_RATE = "rate"
#: Review session: stop the session (payload: ``item_id`` of the card on
#: screen). Unrated due cards stay due — no penalty (design §7).
ACTION_STOP = "stop"

#: Actions the callback handler understands.
ACTIONS = frozenset(
    {
        ACTION_ADD,
        ACTION_SAVE,
        ACTION_REGEN,
        ACTION_SKIP,
        ACTION_BOOST,
        ACTION_LANG,
        ACTION_SHOW,
        ACTION_RATE,
        ACTION_STOP,
    }
)

#: Sentinel payload for actions that carry no data (skip).
NO_PAYLOAD = "-"

#: Length of the short opaque references embedded in payloads (tokens).
TOKEN_LEN = 8


def new_callback_id() -> str:
    """A unique per-button id (16 hex chars — 64-bit uniqueness is ample)."""
    return uuid.uuid4().hex[:16]


def new_token() -> str:
    """A short opaque store reference for one candidate / confirmation."""
    return uuid.uuid4().hex[:TOKEN_LEN]


def make_callback_data(action: str, payload: str, callback_id: str) -> str:
    """``action:payload:callback_id`` (one nonce per button instance)."""
    return f"{action}:{payload}:{callback_id}"


def parse_callback_data(data: str | None) -> Optional[tuple[str, str, str]]:
    """``(action, payload, callback_id)`` or ``None`` when malformed/foreign.

    Parsing is anchored on the colons: the action ends at the first colon,
    the ``callback_id`` is the last segment, and everything in between is
    the payload — so an unusual payload (or foreign data shaped like ours)
    cannot crash parsing. Malformed data (fewer than two colons, empty
    action/id, unknown action) is ignored by the caller — never an
    exception that would surface as a bot error.
    """
    if not isinstance(data, str):
        return None
    first = data.find(":")
    last = data.rfind(":")
    if first <= 0 or last <= first:
        return None
    action = data[:first]
    payload = data[first + 1 : last]
    callback_id = data[last + 1 :]
    if not payload or not callback_id or action not in ACTIONS:
        return None
    return action, payload, callback_id
