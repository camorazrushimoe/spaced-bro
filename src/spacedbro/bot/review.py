"""On-demand review session (BON-32, design §7, tasks.md §4.6).

User flow (design §7 "Review session & backlog"):

1. Entry: ``/review`` or a natural-language trigger. The bot reports
   **how many items are due** — ``next_review_at <= now`` via the
   BON-29 due query (``ItemRepository.due``) — and presents the FIRST
   card: front only, with [Show answer]. Nothing is revealed before
   the user asks.
2. One card at a time: [Show answer] flips the card (front + back) and
   offers the four quality buttons Again / Hard / Good / Easy plus a
   [Stop] to bail out early.
3. After each rating the **pure SRS engine** (BON-28,
   ``spacedbro.srs.advance(state, quality, now)``) computes the new
   state and the **repository** (BON-29, ``ItemRepository.update_srs``)
   persists exactly what the engine returned — no scheduling logic in
   the bot layer. Then the bot offers the NEXT due card (the due queue
   is re-read from the database after every rating — "proactive and
   on-demand share the same due queue") or stops when none remain.

Guarantees from the ticket and design §7 / §8:

- **On-demand reviews do NOT increment ``proactive_count``** — this
  module never calls ``UserRepository.record_proactive``.
- **Unattended due cards stay due, no penalty**: a [Stop] (or session
  expiry) only clears the in-process session state; no SRS field of an
  unrated card changes.
- **Callback idempotency** (design §1): every button carries a unique
  ``callback_id`` (``CallbackLedger`` in ``handlers.handle_callback``
  makes replays a no-op), and every show/rate/stop payload is bound to
  the ``item_id`` on screen — a stale button from a replaced session
  never applies a rating to the wrong card.
- **Errors** (design §9): a mid-session persistence failure keeps the
  card on screen (its state was NOT updated, so nothing is rated
  twice) and offers a retry. No stack traces reach the user.

Card content is never embedded in ``callback_data`` (64-byte limit) —
the payload is only ``quality:item_id`` / ``item_id`` (design §1
callback protocol).
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from spacedbro.bot import callbacks as cb
from spacedbro.bot.services import ensure_profile
from spacedbro.bot.state import ContextStore, ReviewSession
from spacedbro.clock import Clock
from spacedbro.db.models import LearningItem, User
from spacedbro.db.repositories import ItemRepository, UserRepository
from spacedbro.srs import Quality, SRSState, SRSStatus, advance

logger = logging.getLogger(__name__)

# --- Copy (short, friendly, no teacher talk) -----------------------------------

#: design §7: "bot reports how many due" — one short line before the
#: first card.
DUE_INTRO = "You've got <b>{count}</b> card{plural} due — let's go \U0001F680"
#: The first card of a session: front only, nothing revealed yet.
CARD_FRONT = "<b>{front}</b>"
#: After [Show answer]: the full card + quality buttons.
CARD_REVEALED = "<b>{front}</b>\n{back}"
#: Session finished, nothing left due.
SESSION_DONE = "That's all for now — nothing due. Nice work \U0001F389"
#: Session stopped by the user: unattended cards stay due (no penalty).
SESSION_STOPPED = "Stopped \u23F8\uFE0F Your due cards will wait — send /review any time."
#: Empty path (design §7 / telegram-bot spec "Review session — Empty
#: due"): nothing due → one friendly short line.
NOTHING_DUE = "Nothing due right now \U0001F44B Add a word or a photo and I'll queue it."
#: A due card vanished between report and presentation — keep going.
NEXT_CARD = "Next \u27A1"
#: Persistence failed mid-session: state untouched, retry offered.
REVIEW_RETRY = "That didn't save — try that rating again?"
#: Generic mid-session error (design §9: friendly, no stack trace).
REVIEW_ERROR = "Something glitched \U0001F41B Try again in a sec?"
#: A stale button (session moved on / expired / replaced by a new
#: /review): nothing was applied, nothing is revealed.
STALE_CARD = "That card is no longer on screen — send /review to start fresh \U0001F914"

#: Inline-button labels (telegram-bot spec "Inline buttons": Show
#: answer, quality ratings).
BTN_SHOW_ANSWER = "Show answer"
BTN_AGAIN = "Again"
BTN_HARD = "Hard"
BTN_GOOD = "Good"
BTN_EASY = "Easy"
BTN_STOP = "Stop"
BTN_TRY_AGAIN = "Try again"

#: Quality button label → engine quality (design §6, BON-28).
_QUALITY_BY_LABEL = {
    BTN_AGAIN: Quality.AGAIN,
    BTN_HARD: Quality.HARD,
    BTN_GOOD: Quality.GOOD,
    BTN_EASY: Quality.EASY,
}
#: Engine quality value → quality (for parsing rate payloads).
_QUALITY_BY_VALUE = {quality.value: quality for quality in _QUALITY_BY_LABEL.values()}

#: Natural-language review triggers (design §7 "or NL"), matched as a
#: whole message or a leading phrase — see :func:`is_review_intent`.
REVIEW_TRIGGERS = frozenset(
    {
        "review",
        "reviews",
        "let's review",
        "lets review",
        "start review",
        "start reviews",
        "start a review",
        "review my cards",
        "review my words",
        "time to review",
    }
)

#: In-process session key (the ``ContextStore`` TTL bounds a forgotten
#: session; nothing persistent is involved).
_SESSION_KEY = "review"


# --- NL trigger (design §7 "On-demand /review or NL") ------------------------------


def is_review_intent(text: str) -> bool:
    """Whether ``text`` is a natural-language review request.

    Conservative on purpose: only a whole-message review phrase (the
    known triggers) or a leading phrase counts, so a learning request
    that merely contains "review" (``"reviewed this chapter"``) still
    goes to extraction and is never swallowed.
    """
    normalized = " ".join((text or "").casefold().split())
    if normalized in REVIEW_TRIGGERS:
        return True
    # Leading-phrase match: "review now", "let's review tonight", …
    return any(normalized.startswith(phrase + " ") for phrase in REVIEW_TRIGGERS)


# --- Session state helpers ---------------------------------------------------------


def _session(store: ContextStore, tg_id: int) -> ReviewSession | None:
    """The user's in-progress session, or ``None`` (absent/expired)."""
    session = store.get(tg_id, _SESSION_KEY)
    return session if isinstance(session, ReviewSession) else None


def _set_session(store: ContextStore, tg_id: int, session: ReviewSession) -> None:
    store.set(tg_id, _SESSION_KEY, session)


def _end_session(store: ContextStore, tg_id: int) -> None:
    store.pop(tg_id, _SESSION_KEY)


def _next_due_id(
    items: ItemRepository, user_id: int, exclude: tuple[int, ...]
) -> int | None:
    """Id of the next due card to present: the due queue (oldest due
    first) minus the cards already rated in this session, or ``None``.

    The clock is read through the repository's injected clock (the same
    aware-UTC ``now`` the scheduler's due query uses) — no wall clock
    here.
    """
    for item in items.due(user_id):
        if item.id not in exclude:
            return item.id
    return None


def _to_engine_state(item: LearningItem) -> SRSState:
    """DB row → the pure engine's state (BON-28 SRS fields + content)."""
    return SRSState(
        front=item.front,
        back=item.back,
        context=item.context,
        repetitions=item.repetitions,
        ease=item.ease,
        interval_minutes=item.interval_minutes,
        next_review_at=item.next_review_at,
        last_review_at=item.last_review_at,
        status=SRSStatus(item.status),
    )


def _parse_item_id(payload: str) -> int | None:
    """The item id in a show/stop/rate payload, or ``None`` when
    malformed. Rate payloads are ``quality:item_id`` — the id is the
    last segment (quality values never contain colons)."""
    token = payload.rsplit(":", 1)[-1]
    try:
        item_id = int(token)
    except ValueError:
        return None
    return item_id if item_id > 0 else None


def _parse_quality(payload: str) -> Quality | None:
    """The quality in a rate payload (``quality:item_id``), or ``None``
    when the first segment is not a known quality value."""
    return _QUALITY_BY_VALUE.get(payload.split(":", 1)[0])


# --- Presentation (pure text builders + markups) ------------------------------------


def _due_intro_text(count: int) -> str:
    plural = "s" if count != 1 else ""
    return DUE_INTRO.format(count=count, plural=plural)


def _card_front_text(front: str) -> str:
    return CARD_FRONT.format(front=front)


def _card_revealed_text(front: str, back: str) -> str:
    return CARD_REVEALED.format(front=front, back=back)


def _front_markup(item_id: int) -> InlineKeyboardMarkup:
    """[Show answer] under the front (nothing else is revealed yet) plus
    [Stop] to bail out before seeing the answer."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_SHOW_ANSWER,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_SHOW, str(item_id), cb.new_callback_id()
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=BTN_STOP,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_STOP, str(item_id), cb.new_callback_id()
                    ),
                )
            ],
        ]
    )


def _quality_markup(item_id: int) -> InlineKeyboardMarkup:
    """[Again | Hard | Good | Easy] + [Stop] for the revealed card."""
    rating_row = [
        InlineKeyboardButton(
            text=label,
            callback_data=cb.make_callback_data(
                cb.ACTION_RATE, f"{quality.value}:{item_id}", cb.new_callback_id()
            ),
        )
        for label, quality in _QUALITY_BY_LABEL.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            rating_row,
            [
                InlineKeyboardButton(
                    text=BTN_STOP,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_STOP, str(item_id), cb.new_callback_id()
                    ),
                )
            ],
        ]
    )


def _retry_markup(item_id: int) -> InlineKeyboardMarkup:
    """[Try again | Stop] after a failed rating — the card is still on
    screen (its state was NOT updated), so [Try again] re-reveals it
    with the quality buttons for the same card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_TRY_AGAIN,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_SHOW, str(item_id), cb.new_callback_id()
                    ),
                ),
                InlineKeyboardButton(
                    text=BTN_STOP,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_STOP, str(item_id), cb.new_callback_id()
                    ),
                )
            ]
        ]
    )


# --- Entry point ----------------------------------------------------------------------


async def start_review_session(
    message: types.Message,
    *,
    clock: Clock,
    users: UserRepository,
    items: ItemRepository,
    store: ContextStore,
    **_extra: Any,
) -> None:
    """/review or NL trigger: report the due count, then the first card.

    design §7: "bot reports how many due (next_review_at <= now), then
    presents one card at a time". A fresh entry always starts a new
    session (a previous one, if any, is simply dropped — dropping it
    never touches the SRS state of unrated cards: they stay due).
    """
    tg_id = message.from_user.id
    user = ensure_profile(users, clock, tg_id)
    now = clock.utc_now()

    due = items.due(user.id, now)
    if not due:
        # Empty path (telegram-bot spec "Review session — Empty due"):
        # say so briefly; clear any stale session state so a later
        # /review starts clean.
        _end_session(store, tg_id)
        await message.answer(NOTHING_DUE)
        return

    first = due[0]
    _set_session(
        store, tg_id, ReviewSession(total=len(due), rated=(), pending=first.id)
    )
    await message.answer(_due_intro_text(len(due)))
    await message.answer(
        _card_front_text(first.front), reply_markup=_front_markup(first.id)
    )


# --- Callback entry (dispatched by handlers.handle_callback) ---------------------------


async def handle_review_callback(
    callback: types.CallbackQuery,
    *,
    clock: Clock,
    items: ItemRepository,
    store: ContextStore,
    action: str,
    payload: str,
    user: User,
) -> None:
    """Review-session callback: show / rate / stop (design §7).

    Called from ``handlers.handle_callback`` AFTER the callback ledger
    has accepted the id (replays never reach here).
    """
    message = callback.message
    if message is None:
        # The card message is gone (deleted/expired): nothing to show.
        await callback.answer()
        return

    tg_id = callback.from_user.id
    session = _session(store, tg_id)
    item_id = _parse_item_id(payload)

    if session is None or item_id is None:
        # No live session (expired / a new /review replaced it) or a
        # malformed payload — a stale button: short ack, nothing
        # applied, nothing revealed.
        await message.answer(STALE_CARD)
        await callback.answer()
        return

    if session.pending != item_id:
        # Buttons are bound to the card on screen: this button is stale
        # (the session moved on). Never apply a rating to a different
        # card.
        await message.answer(STALE_CARD)
        await callback.answer()
        return

    if action == cb.ACTION_SHOW:
        await _show_answer(message, items, user, item_id)
        return

    if action == cb.ACTION_STOP:
        # Unattended due cards remain due — no penalty (design §7):
        # only the in-process session state is dropped.
        _end_session(store, tg_id)
        await message.answer(SESSION_STOPPED)
        await callback.answer("Stopped")
        return

    if action == cb.ACTION_RATE:
        quality = _parse_quality(payload)
        if quality is None:
            await message.answer(STALE_CARD)
            await callback.answer()
            return
        await _rate_card(
            callback, clock, items, store, user, session, item_id, quality
        )


# --- Action implementations ------------------------------------------------------------


async def _show_answer(
    message: types.Message,
    items: ItemRepository,
    user: User,
    item_id: int,
) -> None:
    """[Show answer]: reveal front + back and offer the quality buttons
    (design §7 "front → show answer → quality"). No SRS state changes —
    the rating is what moves the card."""
    item = items.get(user.id, item_id)
    if item is None:
        # The card vanished (foreign/stale button) — nothing to reveal.
        await message.answer(STALE_CARD)
        return
    await message.answer(
        _card_revealed_text(item.front, item.back),
        reply_markup=_quality_markup(item_id),
    )
    # ``pending`` is unchanged: the same card is still on screen, now
    # with the rating buttons.


async def _rate_card(
    callback: types.CallbackQuery,
    clock: Clock,
    items: ItemRepository,
    store: ContextStore,
    user: User,
    session: ReviewSession,
    item_id: int,
    quality: Quality,
) -> None:
    """Apply one rating: engine → persist → next due card or stop.

    design §7: "After rating, offer next due or stop." The engine is
    the pure BON-28 ``advance``; the repository persists exactly what
    it returned. On-demand reviews do NOT touch the proactive counter
    (design §8) — nothing here calls ``record_proactive``.
    """
    message = callback.message
    if message is None:
        await callback.answer()
        return
    tg_id = callback.from_user.id

    item = items.get(user.id, item_id)
    if item is None:
        await message.answer(STALE_CARD)
        await callback.answer()
        return

    now = clock.utc_now()
    try:
        new_state = advance(_to_engine_state(item), quality, now)
        items.update_srs(
            user.id,
            item_id,
            repetitions=new_state.repetitions,
            ease=new_state.ease,
            interval_minutes=new_state.interval_minutes,
            next_review_at=new_state.next_review_at,
            last_review_at=new_state.last_review_at,
            status=new_state.status,
        )
    except Exception:
        # design §9: server-side log only; the user gets a short line.
        # The card's state was NOT updated (the engine is pure and the
        # repository commits atomically), so the card stays on screen
        # and the rating can simply be retried — no double-apply.
        logger.exception("review rating failed for user %s item %s", user.id, item_id)
        await message.answer(REVIEW_RETRY, reply_markup=_retry_markup(item_id))
        await callback.answer()
        return

    # This card is rated — mark it done and move to the next due card.
    rated = session.rated + (item_id,)
    _set_session(store, tg_id, ReviewSession(total=session.total, rated=rated, pending=0))
    await callback.answer("Noted")

    next_id = _next_due_id(items, user.id, rated)
    if next_id is None:
        # "offer next due or stop" — nothing left due this pass.
        _end_session(store, tg_id)
        await message.answer(SESSION_DONE)
        return

    next_item = items.get(user.id, next_id)
    if next_item is None:  # pragma: no cover - row vanished between queries
        _end_session(store, tg_id)
        await message.answer(SESSION_DONE)
        return

    _set_session(
        store, tg_id, ReviewSession(total=session.total, rated=rated, pending=next_id)
    )
    await message.answer(f"{NEXT_CARD} ({len(rated)}/{session.total})")
    await message.answer(
        _card_front_text(next_item.front), reply_markup=_front_markup(next_id)
    )
