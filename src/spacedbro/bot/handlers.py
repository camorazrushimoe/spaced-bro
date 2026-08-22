"""Telegram handlers for the add flow (BON-31).

Implements the user-facing loop of ``openspec/changes/mvp-core/design.md``:

- §1 Gateway: ``/start`` + text + photo + callbacks, short replies,
  inline buttons, and callback idempotency via a unique ``callback_id``
  (a replayed id is a no-op).
- §4 User profile: ``/start`` asks the target language **once**
  (default ``en``); ``native_lang`` defaults to ``ru``.
- §3 Intent & extraction: learning text → candidates + [Add] buttons;
  non-learning text → short ack + hint, **no candidates**.
- §5 Add flow: candidate → duplicate check (notify + Boost, no second
  row) → cheap LLM one-line ``back`` into ``native_lang`` → show
  front+back with [Save] [Wrong — regenerate] [Skip]; only Save persists
  with the New SRS state (BON-28 engine constants, BON-29 repository).
- §2 Images: photo → multimodal LLM → 1–3 candidates → same add path;
  bytes are process-and-discard (never stored).
- §9 Errors: LLM/API failures → short retry message, no stack traces,
  no partial save. Voice → "not supported yet" reply.

Callback protocol (design §1): every actionable button carries a unique
random ``callback_id`` nonce; the payload is a **short opaque reference**
(candidates / confirmation token, item id, language code) — card content
is never embedded in ``callback_data`` (64-byte Telegram limit), it is
looked up in the in-process store by token.

Wiring: aiogram 3.30 passes the dispatcher's ``workflow_data`` to
handlers as **keyword arguments**, so each handler names exactly the
dependencies it uses (``clock``, ``users``, ``items``, ``store``,
``ledger``, ``llm_client``, ``bot``); a ``**_extra`` catch-all absorbs
the dispatcher's own extras (``bot``, ``handler``, ``update``). Tests
invoke the same module-level functions with the full dependency dict —
no live bot, no network, no wall clock.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.exc import IntegrityError

from spacedbro.bot import callbacks as cb
from spacedbro.bot.services import (
    LLMCaller,
    extract_candidates,
    extract_from_image,
    generate_back,
)
from spacedbro.bot.state import Candidate, CallbackLedger, Confirmation, ContextStore
from spacedbro.clock import Clock
from spacedbro.db.models import User
from spacedbro.db.repositories import ItemNotFoundError, ItemRepository, UserRepository
from spacedbro.llm.errors import (
    LLMError,
    ProviderUnavailableError,
    RateLimitError,
    TimeoutError,
    VisionNotSupportedError,
)

logger = logging.getLogger(__name__)

# --- Copy (SpacedBro tone: short, friendly, no teacher talk) -----------------

WELCOME = (
    "Yo, I'm SpacedBro \U0001F919\n"
    "Send me a word or a photo — I'll turn it into a flashcard."
)
ONBOARDING_QUESTION = (
    "Quick one: which language are we learning?\n"
    "Reply with a name (en, de, es, fr, …)"
)
ONBOARDING_REASK = (
    "That one's not a language I know 🤔\n"
    "Reply with a code (en, de, es, fr, …) — or just say Skip."
)
ONBOARDING_DEFAULTED = (
    "No problem — going with <b>English</b> \U0001F1EC\U0001F1E7\n"
    "Send me a word or a photo to get started!"
)
LANG_CONFIRM = (
    "You're now learning <b>{target}</b> \U0001F3AF\n"
    "Send me a word or a photo!"
)
TEXT_HINT = (
    "Got it \U0001F44D\n"
    "Send me a word or a photo to learn it — or send /review for your due cards."
)
CANDIDATES_INTRO = "Here's what I found — tap <b>Add</b> on the ones you want:"
IMAGE_NOTHING = "Couldn't read anything useful in that photo \U0001F937 Try a clearer one."
IMAGE_FAILED = "Couldn't process that photo. Try another one?"
VISION_UNAVAILABLE = "Photos aren't supported in this setup yet \U0001F648 Send me text instead."
VOICE_REPLY = "Voice isn't supported yet \U0001F648 Send me text or a photo instead."
RETRY_GENERIC = "That didn't work. Try again in a sec?"
RETRY_LLM = "The service is slow right now — try again in a bit?"
BACK_FAILED = "Couldn't generate the meaning. Want to try again?"
BOOST_APPLIED = "Boosted \u26A1 — back to the start of the queue."
SAVED = "Saved \u2705 First review in ~20 min."
SKIPPED = "Skipped — nothing saved."
DUPLICATE_LINE = 'You already know "{front}" \U0001F9E0 Want to boost it?'
NOPE = "Nothing to do here — send me a word or a photo!"

#: Inline-button labels (telegram-bot spec "Inline buttons").
BTN_SAVE = "Save"
BTN_REGEN = "Wrong — regenerate"
BTN_SKIP = "Skip"
BTN_ADD = "Add"
BTN_BOOST = "Boost"
BTN_RETRY = "Try again"
BTN_EN = "English (default)"
BTN_SKIP_Q = "Skip"


# --- Small pure helpers --------------------------------------------------------


def _esc(text: str) -> str:
    """HTML-escape untrusted text (user input / LLM output) for HTML mode."""
    return html.escape(text, quote=False)


def _card_text(front: str, back: str, context: str | None) -> str:
    """The confirm-back message: front (bold) + optional context + back."""
    lines = [f"<b>{_esc(front)}</b>"]
    if context:
        lines.append(f"<i>{_esc(context)}</i>")
    lines.append(_esc(back))
    return "\n".join(lines)


def _candidate_buttons(candidates: list[tuple[str, Candidate]]) -> InlineKeyboardMarkup:
    """One row of [Add | Skip] per candidate (up to 3, design §2/§3).

    The payload is a short opaque token bound to one candidate in the
    store — never the word itself (64-byte callback limit).
    """
    rows: list[list[InlineKeyboardButton]] = []
    for token, candidate in candidates:
        rows.append(
            [
                InlineKeyboardButton(
                    text=BTN_ADD,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_ADD, token, cb.new_callback_id()
                    ),
                ),
                InlineKeyboardButton(
                    text=BTN_SKIP,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_SKIP, cb.NO_PAYLOAD, cb.new_callback_id()
                    ),
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirmation_markup(key: str) -> InlineKeyboardMarkup:
    """Save / Regenerate / Skip for the pending confirmation.

    All three carry the confirmation ``key`` (save/regen) so they bind to
    exactly the card on screen; a regenerated confirmation issues a fresh
    key, which invalidates the previous buttons.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_SAVE,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_SAVE, key, cb.new_callback_id()
                    ),
                ),
                InlineKeyboardButton(
                    text=BTN_REGEN,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_REGEN, key, cb.new_callback_id()
                    ),
                ),
                InlineKeyboardButton(
                    text=BTN_SKIP,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_SKIP, cb.NO_PAYLOAD, cb.new_callback_id()
                    ),
                ),
            ]
        ]
    )


def _boost_markup(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_BOOST,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_BOOST, str(item_id), cb.new_callback_id()
                    ),
                ),
                InlineKeyboardButton(
                    text=BTN_SKIP,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_SKIP, cb.NO_PAYLOAD, cb.new_callback_id()
                    ),
                ),
            ]
        ]
    )


def _retry_add_markup(token: str) -> InlineKeyboardMarkup:
    """Retry = re-run Add for the same candidate, fresh callback id."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_RETRY,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_ADD, token, cb.new_callback_id()
                    ),
                ),
                InlineKeyboardButton(
                    text=BTN_SKIP,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_SKIP, cb.NO_PAYLOAD, cb.new_callback_id()
                    ),
                ),
            ]
        ]
    )


def _lang_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_EN,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_LANG, "en", cb.new_callback_id()
                    ),
                ),
                InlineKeyboardButton(
                    text=BTN_SKIP_Q,
                    callback_data=cb.make_callback_data(
                        cb.ACTION_LANG, "skip", cb.new_callback_id()
                    ),
                ),
            ]
        ]
    )


def _friendly_llm_error(exc: LLMError) -> str:
    """design §9: short user-facing line per failure kind, never a stack trace."""
    if isinstance(exc, (TimeoutError, RateLimitError, ProviderUnavailableError)):
        return RETRY_LLM
    return RETRY_GENERIC


#: Language codes the bot knows (free-text onboarding answers).
_LANG_CODES = frozenset(
    {
        "en", "es", "fr", "de", "it", "pt", "nl", "pl", "sv", "da", "no",
        "fi", "cs", "hu", "ro", "tr", "ru", "uk", "ja", "ko", "zh", "he",
        "ar", "hi", "el", "fa",
    }
)

#: Language names → code (what users actually type).
_LANG_NAME_TO_CODE = {
    "english": "en",
    "spanish": "es", "español": "es",
    "french": "fr", "français": "fr",
    "german": "de", "deutsch": "de",
    "italian": "it", "italiano": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "polish": "pl",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "czech": "cs",
    "hungarian": "hu",
    "romanian": "ro",
    "turkish": "tr",
    "russian": "ru",
    "ukrainian": "uk",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "hebrew": "he",
    "arabic": "ar",
    "hindi": "hi",
    "greek": "el",
    "persian": "fa",
}


def _parse_lang_code(text: str) -> str | None:
    """Free-text onboarding answer → known language code, or ``None``.

    Only **known** codes (``en``, ``de``, …) and names (``english``,
    ``deutsch``, …) are accepted — a word that merely looks like a code
    (``cat``) is NOT a language answer, so a first word to learn can never
    be misfiled as the target language (design §4: onboarding asks the
    target language once, default ``en``).
    """
    # The first word carries the answer ("German please!" → "german").
    words = " ".join(text.casefold().split()).strip(".!?").split()
    token = words[0] if words else ""
    if token in _LANG_NAME_TO_CODE:
        return _LANG_NAME_TO_CODE[token]
    if token in _LANG_CODES:
        return token
    return None


def _ensure_profile(users: UserRepository, clock: Clock, tg_id: int) -> tuple[User, bool]:
    """Profile row + whether it was just created (design §4 first interaction)."""
    user = users.profile(tg_id)
    created = False
    if user is None:
        users.get_or_create(tg_id, clock.utc_now())
        user = users.profile(tg_id)
        created = True
    if user is None:
        raise RuntimeError(f"profile for telegram id {tg_id} missing after creation")
    users.touch_activity(user.id, clock.utc_now())
    return user, created


def _store_candidates(
    store: ContextStore, tg_id: int, candidates: list[Candidate]
) -> list[tuple[str, Candidate]]:
    """Persist the candidate list + one opaque token per candidate.

    Returns the ``(token, candidate)`` pairs used to build the buttons.
    """
    pairs = [(cb.new_token(), c) for c in candidates]
    store.set(tg_id, "candidates", dict(pairs))
    return pairs


# --- Router (production wiring) --------------------------------------------------


def build_add_flow_router() -> Router:
    """Router with all add-flow handlers; dependencies arrive as kwargs."""
    router = Router()
    router.message.register(handle_start, CommandStart())
    router.message.register(handle_text, F.text)
    router.message.register(handle_photo, F.photo)
    router.message.register(handle_voice, F.voice | F.audio)
    router.callback_query.register(handle_callback, F.data)
    return router


# --- Entry handlers (module-level: router and tests share exactly these) --------


async def handle_start(
    message: types.Message,
    *,
    clock: Clock,
    users: UserRepository,
    store: ContextStore,
    **_extra: Any,
) -> None:
    """/start — welcome, create profile if needed, ask target language ONCE."""
    tg_id = message.from_user.id
    user, created = _ensure_profile(users, clock, tg_id)

    if created or not user.onboarding_asked:
        users.mark_onboarding_asked(user.id)
        store.set(tg_id, "pending_lang", True)
        await message.answer(
            f"{WELCOME}\n\n{ONBOARDING_QUESTION}", reply_markup=_lang_markup()
        )
    else:
        await message.answer(f"{WELCOME}\n\n{TEXT_HINT}")


async def handle_text(
    message: types.Message,
    *,
    clock: Clock,
    users: UserRepository,
    store: ContextStore,
    llm_client: LLMCaller,
    **_extra: Any,
) -> None:
    """Text — onboarding answer, extraction → candidates, or short ack."""
    if not message.text:
        return
    tg_id = message.from_user.id
    user, _ = _ensure_profile(users, clock, tg_id)

    # Onboarding answer (asked once — never again, design §4). The
    # pending flag is popped inside _answer_onboarding only when the
    # question is resolved, so a re-ask still routes here.
    if store.get(tg_id, "pending_lang"):
        await _answer_onboarding(message, users, user, store, message.text)
        return

    try:
        candidates = await extract_candidates(
            llm_client,
            message.text,
            target_lang=user.target_lang,
            native_lang=user.native_lang,
        )
    except LLMError as exc:
        logger.warning("text extraction failed for user %s: %s", tg_id, exc.detail)
        await message.answer(_friendly_llm_error(exc))
        return

    if not candidates:
        # Non-learning text: short ack + hint, NO candidates (design §3).
        await message.answer(TEXT_HINT)
        return

    pairs = _store_candidates(store, tg_id, candidates)
    await message.answer(CANDIDATES_INTRO, reply_markup=_candidate_buttons(pairs))


async def handle_photo(
    message: types.Message,
    *,
    clock: Clock,
    users: UserRepository,
    store: ContextStore,
    llm_client: LLMCaller,
    bot: Any,
    **_extra: Any,
) -> None:
    """Photo — vision extraction → same add path; bytes process-and-discard."""
    if not message.photo:
        return
    tg_id = message.from_user.id
    user, _ = _ensure_profile(users, clock, tg_id)

    # Largest photo size; bytes are process-and-discard (never stored).
    largest = message.photo[-1]
    try:
        file = await bot.get_file(largest.file_id)
        # aiogram 3.x returns an in-memory BinaryIO stream (not bytes).
        stream = await bot.download_file(file.file_path)
        image_bytes = stream.read() if stream is not None else None
    except Exception as exc:  # download/network — friendly, no stack trace
        logger.warning(
            "photo download failed for user %s: %s: %s",
            tg_id,
            type(exc).__name__,
            exc,
        )
        await message.answer(IMAGE_FAILED)
        return
    if not image_bytes:
        await message.answer(IMAGE_FAILED)
        return
    try:
        candidates = await extract_from_image(
            llm_client,
            image_bytes,
            target_lang=user.target_lang,
            native_lang=user.native_lang,
        )
    except LLMError as exc:
        logger.warning("image extraction failed for user %s: %s", tg_id, exc.detail)
        if isinstance(exc, VisionNotSupportedError):
            await message.answer(VISION_UNAVAILABLE)
        else:
            await message.answer(IMAGE_FAILED)
        return

    if not candidates:
        await message.answer(IMAGE_NOTHING)
        return

    pairs = _store_candidates(store, tg_id, candidates)
    await message.answer(CANDIDATES_INTRO, reply_markup=_candidate_buttons(pairs))


async def handle_voice(message: types.Message, **_extra: Any) -> None:
    """Voice — MUST get a short 'not supported yet' reply (design §9)."""
    if not (message.voice or message.audio):
        return
    await message.answer(VOICE_REPLY)


async def handle_callback(
    callback: types.CallbackQuery,
    *,
    clock: Clock,
    users: UserRepository,
    items: ItemRepository,
    store: ContextStore,
    ledger: CallbackLedger,
    llm_client: LLMCaller,
    **_extra: Any,
) -> None:
    """Every actionable callback — idempotent via the callback ledger."""
    parsed = cb.parse_callback_data(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    action, payload, callback_id = parsed
    tg_id = callback.from_user.id
    user, _ = _ensure_profile(users, clock, tg_id)

    if not ledger.first_seen(callback_id):
        # Replay (double-tap / redelivery): no-op — short ack only.
        await callback.answer("Already handled \U0001F44D")
        return

    if callback.message is None:
        # Stale callback — the message was deleted/expired, so there is
        # nothing to reply to; stop the client spinner and move on.
        await callback.answer()
        return

    if action == cb.ACTION_LANG:
        await _handle_lang(callback, users, store, user, payload)
    elif action == cb.ACTION_ADD:
        await _handle_add(callback, items, store, llm_client, user, payload)
    elif action == cb.ACTION_SAVE:
        await _handle_save(callback, items, store, user, payload)
    elif action == cb.ACTION_REGEN:
        await _handle_regen(callback, items, store, llm_client, user, payload)
    elif action == cb.ACTION_SKIP:
        store.pop(tg_id, "confirm")
        await callback.message.answer(SKIPPED)
        await callback.answer("Skipped")
    elif action == cb.ACTION_BOOST:
        await _handle_boost(callback, items, user, payload)
    else:
        await callback.answer()


# --- Action implementations -------------------------------------------------------


async def _answer_onboarding(
    message: types.Message,
    users: UserRepository,
    user: User,
    store: ContextStore,
    reply_text: str,
) -> None:
    """Resolve the once-only target-language question (design §4).

    A known language (code or name) sets it; an unrecognisable answer
    (``cat`` — a word to learn, not a language) gets ONE short re-ask,
    after which the default ``en`` applies ("default English if skipped").
    The question is never asked more than twice in total.
    """
    reasked = store.get(message.from_user.id, "onboard_reasked")
    code = _parse_lang_code(reply_text)
    if code is not None:
        users.set_target_lang(user.id, code)
        store.pop(message.from_user.id, "pending_lang")
        store.pop(message.from_user.id, "onboard_reasked")
        await message.answer(LANG_CONFIRM.format(target=_esc(code)))
        return
    if not reasked:
        # Keep "pending_lang" set: the next text message is still the
        # answer (the question was not yet resolved).
        store.set(message.from_user.id, "onboard_reasked", True)
        await message.answer(ONBOARDING_REASK)
        return
    users.set_target_lang(user.id, "en")
    store.pop(message.from_user.id, "pending_lang")
    store.pop(message.from_user.id, "onboard_reasked")
    await message.answer(ONBOARDING_DEFAULTED)


async def _handle_lang(
    callback: types.CallbackQuery,
    users: UserRepository,
    store: ContextStore,
    user: User,
    payload: str,
) -> None:
    store.pop(callback.from_user.id, "pending_lang")
    if payload == "skip":
        await callback.message.answer(ONBOARDING_DEFAULTED)
        await callback.answer("English it is")
        return
    users.set_target_lang(user.id, payload)
    await callback.message.answer(LANG_CONFIRM.format(target=_esc(payload)))
    await callback.answer("Locked in")


async def _handle_add(
    callback: types.CallbackQuery,
    items: ItemRepository,
    store: ContextStore,
    llm_client: LLMCaller,
    user: User,
    token: str,
) -> None:
    tg_id = callback.from_user.id
    by_token = store.get(tg_id, "candidates")
    candidate = by_token.get(token) if isinstance(by_token, dict) else None
    if candidate is None:
        # Stale button (candidates replaced / expired) — short nudge.
        await callback.message.answer(NOPE)
        await callback.answer()
        return

    front = candidate.front
    context = candidate.context

    existing_id = items.find_by_front(user.id, front)
    if existing_id is not None:
        # Duplicate: notify + Boost offer — no second row (design §5, step 2).
        await callback.message.answer(
            DUPLICATE_LINE.format(front=_esc(front)),
            reply_markup=_boost_markup(existing_id),
        )
        await callback.answer("Already known")
        return

    try:
        back = await generate_back(
            llm_client,
            front,
            context,
            target_lang=user.target_lang,
            native_lang=user.native_lang,
        )
    except LLMError as exc:
        # design §9: short retry message; nothing is saved.
        logger.warning("back generation failed for %r: %s", front, exc.detail)
        await callback.message.answer(BACK_FAILED, reply_markup=_retry_add_markup(token))
        await callback.answer()
        return

    confirm_key = cb.new_token()
    store.set(
        tg_id,
        "confirm",
        Confirmation(front=front, back=back, context=context, key=confirm_key),
    )
    await callback.message.answer(
        _card_text(front, back, context),
        reply_markup=_confirmation_markup(confirm_key),
    )
    await callback.answer()


async def _handle_save(
    callback: types.CallbackQuery,
    items: ItemRepository,
    store: ContextStore,
    user: User,
    key: str,
) -> None:
    tg_id = callback.from_user.id
    confirm: Confirmation | None = store.get(tg_id, "confirm")
    if confirm is None or confirm.key != key:
        # Stale or unknown confirmation — nothing to persist.
        await callback.message.answer(NOPE)
        await callback.answer()
        return
    front = confirm.front

    try:
        # Re-check at persistence time (a card may have been saved in the
        # meantime): never create a second row.
        if items.is_duplicate(user.id, front):
            existing_id = items.find_by_front(user.id, front)
            if existing_id is None:  # pragma: no cover - race artifact
                await callback.message.answer(NOPE)
                await callback.answer()
                return
            await callback.message.answer(
                DUPLICATE_LINE.format(front=_esc(front)),
                reply_markup=_boost_markup(existing_id),
            )
            await callback.answer("Already known")
            return
        items.save(user.id, front, back=confirm.back, context=confirm.context)
    except IntegrityError:
        # UNIQUE(user_id, normalized_front) fired: a duplicate row exists
        # (created between the pre-check and Save) — the design's
        # "notify + Boost, no second row" path, never a generic error.
        logger.warning("save raced into a duplicate for user %s", tg_id)
        store.pop(tg_id, "confirm")
        # The failed INSERT leaves the transaction open — roll back before
        # re-querying for the duplicate row.
        items.session.rollback()
        existing_id = items.find_by_front(user.id, front)
        if existing_id is None:  # pragma: no cover - race artifact
            await callback.message.answer(RETRY_GENERIC)
            await callback.answer()
            return
        await callback.message.answer(
            DUPLICATE_LINE.format(front=_esc(front)),
            reply_markup=_boost_markup(existing_id),
        )
        await callback.answer("Already known")
        return
    except Exception:
        # Server-side log only; the user gets a short line (design §9).
        logger.exception("persist failed for front %r", front)
        await callback.message.answer(RETRY_GENERIC)
        await callback.answer()
        return

    store.pop(tg_id, "confirm")
    await callback.message.answer(SAVED)
    await callback.answer("Saved")


async def _handle_regen(
    callback: types.CallbackQuery,
    items: ItemRepository,
    store: ContextStore,
    llm_client: LLMCaller,
    user: User,
    key: str,
) -> None:
    tg_id = callback.from_user.id
    confirm: Confirmation | None = store.get(tg_id, "confirm")
    if confirm is None or confirm.key != key:
        await callback.message.answer(NOPE)
        await callback.answer()
        return
    front = confirm.front

    try:
        back = await generate_back(
            llm_client,
            front,
            confirm.context,
            target_lang=user.target_lang,
            native_lang=user.native_lang,
        )
    except LLMError as exc:
        logger.warning("back regeneration failed for %r: %s", front, exc.detail)
        await callback.message.answer(_friendly_llm_error(exc))
        await callback.answer()
        return

    # Fresh key: the new buttons bind to the new card, the old ones die.
    confirm_key = cb.new_token()
    store.set(
        tg_id,
        "confirm",
        Confirmation(front=front, back=back, context=confirm.context, key=confirm_key),
    )
    await callback.message.answer(
        _card_text(front, back, confirm.context),
        reply_markup=_confirmation_markup(confirm_key),
    )
    await callback.answer("Regenerated")


async def _handle_boost(
    callback: types.CallbackQuery,
    items: ItemRepository,
    user: User,
    payload: str,
) -> None:
    try:
        item_id = int(payload)
    except ValueError:
        await callback.answer()
        return
    try:
        items.boost(user.id, item_id)
    except (ItemNotFoundError, LookupError):
        # The item no longer exists or isn't this user's — nothing to boost.
        await callback.message.answer(NOPE)
        await callback.answer()
        return
    await callback.message.answer(BOOST_APPLIED)
    await callback.answer("Boosted")
