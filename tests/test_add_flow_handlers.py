"""Add-flow handler tests (BON-31) — the end-to-end user journey at the
handler seam: real in-memory SQLite repositories, fake LLM client, fake
bot, constructed aiogram updates. Covers every acceptance criterion of the
ticket and the specs (telegram-bot, learning-items, user-memory, media):

- /start onboarding (ask once, default en, native_lang=ru)
- text extraction → candidates; non-learning text → ack, no candidates
- photo → vision → same path; process-and-discard
- confirm-back: Save persists New SRS state; Regenerate; Skip
- duplicate → notify + Boost, no second row; boost resets SRS state
- callback idempotency (replayed id = no-op)
- back-LLM failure → short error, no save, retry offered
- voice → "not supported yet"

Outgoing replies are recorded by patching ``Message.answer`` /
``CallbackQuery.answer`` at class level (the bot is not mounted, so the
real methods would raise); the autouse fixture hands each test the
recording list.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from spacedbro.bot import callbacks as cb
from spacedbro.bot.handlers import (
    SAVED,
    BOOST_APPLIED,
    DUPLICATE_LINE,
    ONBOARDING_QUESTION,
    SKIPPED,
    VOICE_REPLY,
    handle_callback,
    handle_photo,
    handle_start,
    handle_text,
    handle_voice,
)
from spacedbro.db.models import ItemStatus
from spacedbro.llm.errors import (
    InvalidResponseError,
    ProviderUnavailableError,
    TimeoutError,
    VisionNotSupportedError,
)
from spacedbro.srs import NEW_EASE, NEW_INTERVAL_MINUTES

from .addflow_fixtures import (
    FakeBot,
    FakeLLM,
    NOW,
    TG_ID,
    fake_llm,  # noqa: F401  (fixture)
    get_items,
    get_user,
    make_callback,
    make_deps,
    make_message,
    make_photo_message,
    session,  # noqa: F401  (fixture)
)


@pytest.fixture(autouse=True)
def record_answers():
    """Patch Message.answer / CallbackQuery.answer to record instead of HTTP."""
    sent: list[dict] = []
    answered: list[str] = []

    async def fake_answer(self, text, reply_markup=None, **_kw):
        sent.append({"text": text, "markup": reply_markup, "message": self})

    async def fake_cb_answer(self, text=None, **_kw):
        answered.append(text or "")

    Message.answer = fake_answer
    CallbackQuery.answer = fake_cb_answer
    yield {"sent": sent, "answered": answered}
    del Message.answer
    del CallbackQuery.answer


def _button(markup, label: str):
    for row in markup.inline_keyboard:
        for b in row:
            if b.text == label:
                return b
    raise AssertionError(f"button {label!r} not found in {markup}")


async def _press(deps, button, record_answers) -> list[dict]:
    """Click an inline button; returns the replies sent after the click."""
    before = len(record_answers["sent"])
    await handle_callback(
        make_callback(data=button.callback_data, message=make_message(message_id=900)), **deps
    )
    return record_answers["sent"][before:]


async def _start_onboarded(deps, record_answers) -> None:
    """/start for a fresh user + answer the language question with "de"."""
    await handle_start(make_message(text="/start"), **deps)
    await handle_text(make_message(text="de", message_id=2), **deps)


async def _add_candidate(
    deps, record_answers, front: str, context: str | None = None, raw_text: str | None = None
) -> list[dict]:
    """Send a learning text and click its [Add]; returns the Add replies
    (the confirmation / duplicate / error message — not the intro).

    ``front`` is what the LLM extracts; ``raw_text`` is what the user typed
    (defaults to ``front``). The press uses the intro's real Add button —
    the same callback id the user would tap.
    """
    deps["llm_client"].extracts.append([{"front": front, "context": context}])
    await handle_text(
        make_message(text=raw_text if raw_text is not None else front, message_id=3), **deps
    )
    add_btn = _button(record_answers["sent"][-1]["markup"], "Add")
    before = len(record_answers["sent"])
    await handle_callback(
        make_callback(data=add_btn.callback_data, message=make_message(message_id=901)), **deps
    )
    return record_answers["sent"][before:]


async def _save_last_confirmation(deps, record_answers) -> None:
    """Save the most recent confirmation (helper for duplicate/boost tests)."""
    await _press(deps, _button(record_answers["sent"][-1]["markup"], "Save"), record_answers)


# --- /start (design §1 + §4, user-memory "Onboarding question") ------------------


async def test_start_new_user_creates_profile_and_asks_lang(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await handle_start(make_message(text="/start"), **deps)

    user = get_user(session)
    assert user.native_lang == "ru"  # spec default
    assert user.target_lang == "en"  # spec default
    assert user.onboarding_asked is True  # asked ONCE — flagged
    sent = record_answers["sent"]
    assert len(sent) == 1
    assert ONBOARDING_QUESTION in sent[0]["text"]
    buttons = [b.text for row in sent[0]["markup"].inline_keyboard for b in row]
    assert "English (default)" in buttons and "Skip" in buttons


async def test_start_existing_user_is_plain_welcome_no_question(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await handle_start(make_message(text="/start"), **deps)
    await handle_text(make_message(text="de", message_id=2), **deps)
    record_answers["sent"].clear()
    await handle_start(make_message(text="/start", message_id=3), **deps)

    sent = record_answers["sent"]
    assert len(sent) == 1
    assert ONBOARDING_QUESTION not in sent[0]["text"]
    assert get_user(session).onboarding_asked is True


# --- Onboarding answer: text reply + language buttons -----------------------------


async def test_onboarding_text_answer_sets_target_lang(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await handle_start(make_message(text="/start"), **deps)
    await handle_text(make_message(text="de", message_id=2), **deps)

    assert get_user(session).target_lang == "de"
    record_answers["sent"].clear()
    # The question must never be asked again (ask once).
    await handle_text(make_message(text="hello friend", message_id=3), **deps)
    assert get_user(session).target_lang == "de"
    assert all(ONBOARDING_QUESTION not in s["text"] for s in record_answers["sent"])


async def test_onboarding_unparseable_defaults_english(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await handle_start(make_message(text="/start"), **deps)
    await handle_text(make_message(text="hmm not sure", message_id=2), **deps)
    assert get_user(session).target_lang == "en"


async def test_onboarding_lang_button_skip_keeps_default(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await handle_start(make_message(text="/start"), **deps)
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Skip"), record_answers
    )
    assert get_user(session).target_lang == "en"
    assert any("English" in t["text"] for t in replies)


async def test_onboarding_lang_button_sets_code(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await handle_start(make_message(text="/start"), **deps)
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "English (default)"), record_answers
    )
    assert get_user(session).target_lang == "en"
    assert any("You're now learning" in t["text"] for t in replies)


# --- Text: extraction → candidates (design §3) ------------------------------------


async def test_learning_text_shows_candidate_buttons(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.extracts.append([{"front": "apple", "context": "I ate an apple"}])

    await handle_text(make_message(text="I ate an apple", message_id=3), **deps)

    sent = record_answers["sent"]
    assert sent[-1]["text"].startswith("Here's what I found")
    buttons = [b.text for row in sent[-1]["markup"].inline_keyboard for b in row]
    assert buttons == ["Add", "Skip"]


async def test_non_learning_text_short_ack_no_candidates(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.extracts.append([])  # LLM says: not a learning request

    await handle_text(make_message(text="thanks bro", message_id=3), **deps)

    sent = record_answers["sent"]
    assert "Got it" in sent[-1]["text"]
    assert sent[-1]["markup"] is None  # no Add/Skip buttons
    assert fake_llm.complete_calls, "extraction was attempted (intent detection)"
    assert get_items(session) == []  # nothing created


async def test_extraction_failure_is_friendly_and_creates_nothing(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.extract_errors.append(ProviderUnavailableError("HTTP 503"))

    await handle_text(make_message(text="word", message_id=3), **deps)

    sent = record_answers["sent"]
    assert "slow" in sent[-1]["text"]
    assert get_items(session) == []


async def test_word_like_1230_never_in_callback_data(session, fake_llm, record_answers):
    """A word like 12:30 must not appear in callback_data (64-byte limit).

    The Add button carries a short opaque token; the word itself lives in
    the in-process store and is looked up on the callback.
    """
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.extracts.append([{"front": "12:30"}])
    fake_llm.texts.append("время")

    await handle_text(make_message(text="12:30", message_id=3), **deps)
    intro = record_answers["sent"][-1]
    for row in intro["markup"].inline_keyboard:
        for btn in row:
            assert "12:30" not in btn.callback_data
            assert len(btn.callback_data.encode("utf-8")) <= 64

    replies = await _press(deps, _button(intro["markup"], "Add"), record_answers)
    assert any("время" in t["text"] for t in replies)


# --- Photo: vision → same add path, process-and-discard (design §2) ----------------


async def test_photo_vision_candidates_then_same_add_path(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm, bot=FakeBot(b"\x89PNG-test"))
    await handle_start(make_message(text="/start"), **deps)
    fake_llm.vision.append([{"front": "STOP"}])

    await handle_photo(make_photo_message(message_id=3), **deps)

    sent = record_answers["sent"]
    assert sent[-1]["text"].startswith("Here's what I found")
    # The image was sent to the LLM as a data URL and never stored.
    assert deps["llm_client"].vision_calls[0]["image_url"].startswith("data:image/")
    assert get_items(session) == []  # nothing persisted before Save


async def test_photo_unreadable_image_short_message(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    fake_llm.vision.append([])
    await handle_photo(make_photo_message(message_id=3), **deps)
    sent = record_answers["sent"]
    assert "Couldn't read anything useful" in sent[-1]["text"]


async def test_photo_vision_unsupported_gets_own_message(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    fake_llm.vision_errors.append(VisionNotSupportedError("HTTP 404 local"))
    await handle_photo(make_photo_message(message_id=3), **deps)
    sent = record_answers["sent"]
    assert "supported" in sent[-1]["text"] and "text" in sent[-1]["text"].lower()


async def test_photo_download_failure_is_friendly(session, record_answers):
    class BrokenBot:
        async def get_file(self, file_id):
            raise OSError("connection refused")

    fake_llm = FakeLLM()
    deps = make_deps(session, fake_llm, bot=BrokenBot())
    await handle_photo(make_photo_message(message_id=3), **deps)
    sent = record_answers["sent"]
    assert "Couldn't process that photo" in sent[-1]["text"]
    assert get_items(session) == []


# --- Voice (design §9, telegram-bot spec "Voice") ---------------------------------


async def test_voice_replies_not_supported(session, fake_llm, record_answers):
    from aiogram.types import Voice

    deps = make_deps(session, fake_llm)
    msg = make_message(voice=Voice(file_id="v", file_unique_id="vu", duration=3), message_id=3)
    await handle_voice(msg, **deps)
    assert record_answers["sent"][-1]["text"] == VOICE_REPLY


# --- Add → confirm-back → Save (design §5) -----------------------------------------


async def test_add_shows_front_and_back_with_buttons(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")

    replies = await _add_candidate(deps, record_answers, "apple", context="I ate an apple")

    assert len(replies) == 1
    text = replies[0]["text"]
    assert "<b>apple</b>" in text and "яблоко" in text and "I ate an apple" in text
    # Nothing persisted before Save.
    assert get_items(session) == []
    # The confirmation carries the exact three buttons of design §5.
    buttons = [b.text for row in replies[0]["markup"].inline_keyboard for b in row]
    assert "Save" in buttons and any(b.startswith("Wrong") for b in buttons) and "Skip" in buttons


async def test_save_persists_card_with_new_srs_state(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")
    await _save_last_confirmation(deps, record_answers)

    items = get_items(session)
    assert len(items) == 1
    item = items[0]
    assert item.front == "apple"
    assert item.back == "яблоко"
    assert item.normalized_front == "apple"
    # SRS new state (design §6 via the BON-28 constants / BON-29 save).
    assert item.repetitions == 0
    assert item.ease == NEW_EASE
    assert item.interval_minutes == NEW_INTERVAL_MINUTES
    assert item.status == ItemStatus.LEARNING.value
    assert item.next_review_at == NOW + timedelta(minutes=NEW_INTERVAL_MINUTES)
    assert item.next_review_at.tzinfo is not None  # aware UTC
    assert item.last_review_at is None
    assert SAVED in record_answers["sent"][-1]["text"]


async def test_save_replay_is_no_op_no_second_row(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")

    data = _button(record_answers["sent"][-1]["markup"], "Save").callback_data
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=902)), **deps
    )
    # Replay the SAME callback id (double-tap / redelivery).
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=903)), **deps
    )

    assert len(get_items(session)) == 1  # no second row
    assert "Already handled" in record_answers["answered"][-1]


async def test_save_racing_duplicate_shows_boost_not_error(session, fake_llm, record_answers):
    """UNIQUE(user_id, normalized_front) firing between the pre-check and
    the INSERT must take the design's 'notify + Boost, no second row' path
    — not a generic retry error."""
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")

    save_btn = _button(record_answers["sent"][-1]["markup"], "Save")
    items = deps["items"]
    user_id = get_user(session).id
    real_save = items.save
    # Simulate a parallel save landing between the is_duplicate() pre-check
    # and our INSERT: the duplicate check passes, then the INSERT violates
    # the UNIQUE constraint.
    items.is_duplicate = lambda _user_id, _front: False

    def racing_save(*args, **kwargs):
        real_save(user_id, "apple", back="ранее")  # the parallel save
        return real_save(*args, **kwargs)

    items.save = racing_save
    await handle_callback(
        make_callback(data=save_btn.callback_data, message=make_message(message_id=910)), **deps
    )

    assert len(get_items(session)) == 1  # no second row, no partial card
    assert get_items(session)[0].back == "ранее"  # the parallel save's row
    text = record_answers["sent"][-1]["text"]
    assert DUPLICATE_LINE.format(front="apple") in text
    buttons = [b.text for row in record_answers["sent"][-1]["markup"].inline_keyboard for b in row]
    assert "Boost" in buttons


async def test_skip_saves_nothing(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")

    await _press(deps, _button(record_answers["sent"][-1]["markup"], "Skip"), record_answers)

    assert get_items(session) == []
    assert SKIPPED in record_answers["sent"][-1]["text"]


async def test_regen_replaces_back_then_save_uses_new_back(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("bad back")
    await _add_candidate(deps, record_answers, "banana")
    fake_llm.texts.append("хороший back")

    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Wrong — regenerate"), record_answers
    )
    assert "хороший back" in record_answers["sent"][-1]["text"]

    await _press(deps, _button(record_answers["sent"][-1]["markup"], "Save"), record_answers)

    item = get_items(session)[0]
    assert item.back == "хороший back"  # the regenerated back was persisted


async def test_regen_replay_is_no_op(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("first")
    await _add_candidate(deps, record_answers, "pear")

    data = _button(record_answers["sent"][-1]["markup"], "Wrong — regenerate").callback_data
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=904)), **deps
    )
    calls_before = len(fake_llm.complete_calls)
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=905)), **deps
    )
    # Replay: no extra LLM call, no card saved.
    assert len(fake_llm.complete_calls) == calls_before
    assert get_items(session) == []


# --- Duplicate → notify + Boost, no second row (design §5 step 2) -------------------


async def test_duplicate_add_notifies_and_offers_boost_no_second_row(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)

    # First save of "apple".
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")
    await _save_last_confirmation(deps, record_answers)
    assert len(get_items(session)) == 1

    # Second: "  Apple " → the LLM extracts the clean "Apple" → same
    # normalized_front → duplicate path (no second row).
    fake_llm.texts.append("яблоко 2")
    replies = await _add_candidate(deps, record_answers, "Apple", raw_text="  Apple ")
    assert len(get_items(session)) == 1  # NO second row
    assert DUPLICATE_LINE.format(front="Apple") in replies[-1]["text"]
    # Boost + Skip buttons offered.
    buttons = [b.text for row in replies[-1]["markup"].inline_keyboard for b in row]
    assert "Boost" in buttons and "Skip" in buttons


async def test_boost_resets_srs_state_keeps_content(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")
    await _save_last_confirmation(deps, record_answers)
    item = get_items(session)[0]
    # Age the card: advance SRS state (simulates some reviews).
    deps["items"].update_srs(
        user_id=item.user_id,
        item_id=item.id,
        repetitions=3,
        ease=2.6,
        interval_minutes=4320,
        next_review_at=NOW,
        last_review_at=NOW,
        status=ItemStatus.REVIEW.value,
    )
    session.refresh(item)
    assert item.repetitions == 3 and item.status == "review"

    # Duplicate Add → Boost button.
    fake_llm.texts.append("яблоко 2")
    await _add_candidate(deps, record_answers, "Apple")
    await _press(deps, _button(record_answers["sent"][-1]["markup"], "Boost"), record_answers)

    session.refresh(item)
    assert item.repetitions == 0
    assert item.ease == NEW_EASE
    assert item.interval_minutes == NEW_INTERVAL_MINUTES
    assert item.status == ItemStatus.LEARNING.value
    assert item.front == "apple" and item.back == "яблоко"  # content kept
    assert len(get_items(session)) == 1
    assert BOOST_APPLIED in record_answers["sent"][-1]["text"]


async def test_boost_replay_is_no_op(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")
    await _save_last_confirmation(deps, record_answers)
    item = get_items(session)[0]
    deps["items"].update_srs(
        user_id=item.user_id,
        item_id=item.id,
        repetitions=3,
        ease=2.6,
        interval_minutes=4320,
        next_review_at=NOW,
        last_review_at=NOW,
        status=ItemStatus.REVIEW.value,
    )
    session.refresh(item)

    fake_llm.texts.append("яблоко 2")
    await _add_candidate(deps, record_answers, "Apple")
    data = _button(record_answers["sent"][-1]["markup"], "Boost").callback_data
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=906)), **deps
    )
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=907)), **deps
    )

    session.refresh(item)
    assert len(get_items(session)) == 1
    assert "Already handled" in record_answers["answered"][-1]


# --- Back LLM failure (design §9, learning-items "Back LLM failure") ----------------


async def test_back_failure_short_error_no_save_with_retry(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.extracts.append([{"front": "apple"}])
    await handle_text(make_message(text="apple", message_id=3), **deps)
    fake_llm.back_errors.append(TimeoutError("deadline"))  # hits the back call

    await _press(deps, _button(record_answers["sent"][-1]["markup"], "Add"), record_answers)

    assert "Couldn't generate the meaning" in record_answers["sent"][-1]["text"]
    assert get_items(session) == []  # NO partial card
    # Retry button offered with a fresh callback id.
    fake_llm.texts.append("яблоко")
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Try again"), record_answers
    )
    assert any("яблоко" in t["text"] for t in replies)


async def test_back_invalid_response_error_message(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.extracts.append([{"front": "apple"}])
    await handle_text(make_message(text="apple", message_id=3), **deps)
    fake_llm.back_errors.append(InvalidResponseError("HTTP 401"))
    await _press(deps, _button(record_answers["sent"][-1]["markup"], "Add"), record_answers)
    assert get_items(session) == []
    assert "Couldn't generate the meaning" in record_answers["sent"][-1]["text"]


# --- Idempotency: replayed callback id is a no-op (design §1) -----------------------


async def test_replayed_add_does_not_requery_llm(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    fake_llm.texts.append("яблоко")
    await _add_candidate(deps, record_answers, "apple")
    calls_before = len(fake_llm.complete_calls)

    # Replay the exact same Add callback id (double-tap / redelivery):
    # re-read it from the candidate-intro markup that is still on screen.
    intro = record_answers["sent"][-2]  # confirmation is the last one
    add_data = _button(intro["markup"], "Add").callback_data
    await handle_callback(
        make_callback(data=add_data, message=make_message(message_id=908)), **deps
    )
    assert len(fake_llm.complete_calls) == calls_before  # no re-generation
    assert get_items(session) == []
    assert "Already handled" in record_answers["answered"][-1]


# --- Malformed / foreign callback data ---------------------------------------------


async def test_malformed_callback_ignored(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await _start_onboarded(deps, record_answers)
    before = len(record_answers["sent"])
    for data in (
        "garbage",
        "add:apple",
        "add:apple:",
        "unknown:apple:0123456789abcdef",
    ):
        await handle_callback(
            make_callback(data=data, message=make_message(message_id=909)), **deps
        )
    assert len(record_answers["sent"]) == before  # no replies at all
    assert get_items(session) == []


# --- Activity tracking side effect (design §4) --------------------------------------


async def test_any_interaction_touches_activity_utc(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    await handle_start(make_message(text="/start"), **deps)
    user = get_user(session)
    assert user.last_active_at == NOW
    assert user.activity_hours_utc[NOW.hour] == 1
    assert len(user.activity_hours_utc) == 24
