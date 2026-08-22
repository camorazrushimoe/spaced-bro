"""Review-session handler tests (BON-32, design §7, tasks.md §4.6).

The on-demand review loop at the handler seam — real in-memory SQLite
repositories (BON-29), the pure SRS engine (BON-28), constructed aiogram
updates, the same ``record_answers`` seam as the add-flow tests:

- entry: ``/review`` (command) and NL trigger — due count reported
  (``next_review_at <= now``), then ONE card at a time:
  front → [Show answer] → quality rating (Again/Hard/Good/Easy)
- after each rating: ``advance(state, quality, now)`` (BON-28) is
  persisted via ``update_srs`` (BON-29); the bot offers the NEXT due
  card (due queue re-read after every rating) or stops
- on-demand reviews do NOT increment ``proactive_count`` (design §8)
- unattended due cards remain due — [Stop] applies no penalty (design §7)
- empty path: nothing due → one friendly short line
- callback idempotency: replayed rating id = no-op (ledger); stale
  buttons (session replaced / moved on) rate nothing
- mid-session persistence failure: friendly retry, state untouched,
  no double-apply

Helpers in this file seed due cards directly through the repository
(the cards are "old" — saved in the past, due now) and drive the
session through the REAL entry/callback handlers with the real button
payloads the user would tap.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from aiogram.types import CallbackQuery, ErrorEvent, Message, Update
from sqlalchemy.orm import Session

from spacedbro.bot import review
from spacedbro.bot.handlers import (
    handle_callback,
    handle_review,
    handle_start,
    handle_text,
)
from spacedbro.bot.review import (
    CARD_FRONT,
    DUE_INTRO,
    NOTHING_DUE,
    SESSION_DONE,
    SESSION_STOPPED,
    STALE_CARD,
)
from spacedbro.db.models import ItemStatus
from spacedbro.srs import (
    EASY_EASE_BONUS,
    HARD_EASE_PENALTY,
    MAX_INTERVAL_MINUTES,
    Quality,
    advance,
)

from .addflow_fixtures import (
    NOW,
    TG_ID,
    fake_llm,  # noqa: F401  (fixture)
    get_items,
    get_user,
    make_callback,
    make_deps,
    make_message,
    session,  # noqa: F401  (fixture)
)


def make_session():
    """A throwaway in-memory session (per-sub-case isolation inside a
    single test that loops over scenarios)."""
    from spacedbro.db.base import Base
    from spacedbro.db.engine import create_db_engine

    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


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


# --- Helpers -----------------------------------------------------------------


def _button(markup, label: str):
    for row in markup.inline_keyboard:
        for b in row:
            if b.text == label:
                return b
    raise AssertionError(f"button {label!r} not found in {markup}")


def _buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


async def _press(deps, button, record_answers) -> list[dict]:
    """Click an inline button; returns the replies sent after the click."""
    before = len(record_answers["sent"])
    await handle_callback(
        make_callback(data=button.callback_data, message=make_message(message_id=900)), **deps
    )
    return record_answers["sent"][before:]


def _save_due(session: Session, clock, deps: dict, front: str, back: str, **srs) -> int:
    """Seed one DUE card (saved in the past, ``next_review_at`` at or
    before the frozen now) directly through the repository.

    Defaults place the card in the steady state (repetitions=3) so the
    engine's interval math is visible on Good/Easy ratings. ``srs``
    overrides repetitions/ease/interval_minutes/next_review_at/status.
    """
    items = deps["items"]
    deps["users"].get_or_create(TG_ID, clock.utc_now())  # idempotent
    user_id = get_user(session).id
    now = clock.utc_now()
    item = items.save(
        user_id,
        front,
        back=back,
        next_review_at=now - timedelta(hours=1),
        now=now - timedelta(hours=1),
    )
    items.update_srs(
        user_id,
        item.id,
        repetitions=srs.get("repetitions", 3),
        ease=srs.get("ease", 2.5),
        interval_minutes=srs.get("interval_minutes", 4320),
        next_review_at=srs.get("next_review_at", now - timedelta(minutes=30)),
        last_review_at=now - timedelta(hours=1),
        status=srs.get("status", ItemStatus.REVIEW.value),
    )
    return item.id


def _seed_due_cards(session, clock, deps: dict, fronts: list[str]) -> list[int]:
    """Seed several due cards; returns their ids in (due-time, id) order
    — the order the due query returns them (oldest due first)."""
    ids = []
    for i, front in enumerate(fronts):
        ids.append(
            _save_due(
                session,
                clock,
                deps,
                front,
                f"meaning {i}",
                next_review_at=NOW - timedelta(minutes=60 - i),
            )
        )
    return ids


async def _start_session(deps, record_answers, text: str = "/review") -> list[dict]:
    before = len(record_answers["sent"])
    if text == "/review":
        await handle_review(make_message(text=text, message_id=950), **deps)
    else:
        await handle_text(make_message(text=text, message_id=950), **deps)
    return record_answers["sent"][before:]


async def _reveal(deps, record_answers) -> list[dict]:
    return await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Show answer"), record_answers
    )


# --- Entry: due count + first card (design §7) ---------------------------------


async def test_review_reports_due_count_then_first_card_front_only(
    session, fake_llm, record_answers
):
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta", "gamma"])

    replies = await _start_session(deps, record_answers)

    assert len(replies) == 2
    # 1) the due count is reported (3 cards, next_review_at <= now).
    assert DUE_INTRO.format(count=3, plural="s") in replies[0]["text"]
    assert replies[0]["markup"] is None  # no buttons on the count line
    # 2) the FIRST card (oldest due) is presented front-only.
    assert replies[1]["text"] == CARD_FRONT.format(front="alpha")
    # Nothing is revealed before [Show answer].
    assert "meaning 0" not in replies[1]["text"]
    # Only [Show answer] (and [Stop]) is on screen — nothing revealed.
    assert _buttons(replies[1]["markup"]) == ["Show answer", "Stop"]


async def test_review_order_is_oldest_due_first(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    ids = _seed_due_cards(session, deps["clock"], deps, ["a", "b", "c"])
    assert ids == sorted(ids)  # id order == due-time order here

    replies = await _start_session(deps, record_answers)
    assert replies[1]["text"] == CARD_FRONT.format(front="a")  # oldest due


async def test_review_command_is_registered_in_router(session, fake_llm, record_answers):
    from spacedbro.bot.handlers import build_add_flow_router

    router = build_add_flow_router()
    # /review is a command handler ahead of the catch-all text handler.
    handlers = router.message.handlers
    assert any(h.callback is handle_review for h in handlers), (
        "handle_review not registered in the router"
    )
    review_idx = next(i for i, h in enumerate(handlers) if h.callback is handle_review)
    assert any(h.callback is handle_text for i, h in enumerate(handlers) if i > review_idx)


async def test_nl_review_triggers_session(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])

    replies = await _start_session(deps, record_answers, text="Review my words")

    assert DUE_INTRO.format(count=1, plural="") in replies[0]["text"]
    assert replies[1]["text"] == CARD_FRONT.format(front="solo")


@pytest.mark.parametrize(
    "text",
    [
        "review",
        "REVIEW",
        "Let's review",
        "start a review",
        "time to review",
        "review now",
    ],
)
async def test_nl_trigger_variants(session, fake_llm, record_answers, text):
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["x"])

    replies = await _start_session(deps, record_answers, text=text)
    assert "card due" in replies[0]["text"] or "cards due" in replies[0]["text"]


async def test_word_containing_review_is_not_swallowed(
    session, fake_llm, record_answers
):
    """A learning word that merely contains 'review' (as a substring,
    not a trigger phrase) still goes to extraction — the NL trigger must
    be conservative (design §7 'or NL' without eating the add flow)."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["due"])
    fake_llm.extracts.append([{"front": "reviewed"}])

    replies = await _start_session(deps, record_answers, text="I reviewed this chapter")

    # Extraction path, NOT the review session.
    assert replies[-1]["text"].startswith("Here's what I found")
    assert review._session(deps["store"], TG_ID) is None


async def test_nl_trigger_works_during_pending_onboarding(
    session, fake_llm, record_answers
):
    """The language question is asked once at /start; a review trigger
    typed as the 'answer' is not a language — it must NOT be swallowed
    by extraction either: the question is skipped (default ``en`` held)
    and the review session starts (design §4 + §7)."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])

    # /start for a fresh user → language question pending.
    record_answers["sent"].clear()
    await handle_start(make_message(text="/start", message_id=940), **deps)
    assert "which language" in record_answers["sent"][-1]["text"]

    # The "answer" is a review trigger, not a language.
    replies = await _start_session(deps, record_answers, text="review")

    assert get_user(session).target_lang == "en"  # default held
    assert DUE_INTRO.format(count=1, plural="") in replies[0]["text"]


async def test_is_review_intent_is_conservative():
    assert review.is_review_intent("review")
    assert review.is_review_intent("  Review  ")
    assert review.is_review_intent("let's review")
    assert review.is_review_intent("review my cards")
    assert review.is_review_intent("review now")
    assert not review.is_review_intent("")
    assert not review.is_review_intent("hi")
    assert not review.is_review_intent("I reviewed this chapter")
    assert not review.is_review_intent("preview")
    assert not review.is_review_intent("reviewed")
    assert not review.is_review_intent("a review")  # 'review' not leading


async def test_review_empty_path_friendly_short_reply(
    session, fake_llm, record_answers
):
    deps = make_deps(session, fake_llm)
    # Profile exists (the user /start'ed before) but has no cards at all.
    deps["users"].get_or_create(TG_ID, NOW)

    replies = await _start_session(deps, record_answers)

    assert len(replies) == 1
    assert replies[0]["text"] == NOTHING_DUE
    assert replies[0]["markup"] is None


async def test_review_only_future_cards_not_due(
    session, fake_llm, record_answers
):
    """Cards with next_review_at > now are NOT due (boundary: design §7
    'next_review_at <= now')."""
    deps = make_deps(session, fake_llm)
    _save_due(
        session,
        deps["clock"],
        deps,
        "future",
        "later",
        next_review_at=NOW + timedelta(minutes=5),
    )

    replies = await _start_session(deps, record_answers)
    assert replies[0]["text"] == NOTHING_DUE
    assert get_items(session)[0].next_review_at == NOW + timedelta(minutes=5)


async def test_review_boundary_next_review_at_equal_now_is_due(
    session, fake_llm, record_answers
):
    deps = make_deps(session, fake_llm)
    _save_due(
        session, deps["clock"], deps, "edge", "at now", next_review_at=NOW
    )

    replies = await _start_session(deps, record_answers)
    assert DUE_INTRO.format(count=1, plural="") in replies[0]["text"]


# --- Show answer → quality rating (design §7) ------------------------------------


async def test_show_answer_reveals_back_and_quality_buttons(
    session, fake_llm, record_answers
):
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha"])

    replies = await _start_session(deps, record_answers)
    revealed = await _reveal(deps, record_answers)

    assert len(revealed) == 1
    text = revealed[0]["text"]
    assert "<b>alpha</b>" in text and "meaning 0" in text  # front + back
    assert _buttons(revealed[0]["markup"]) == [
        "Again",
        "Hard",
        "Good",
        "Easy",
        "Stop",
    ]
    # No SRS state changed by revealing (only the rating moves the card).
    item = get_items(session)[0]
    assert item.repetitions == 3 and item.last_review_at != NOW


async def test_rate_persists_engine_state_and_offers_next_due(
    session, fake_llm, record_answers
):
    """Full loop: 2 due cards → rate first Good → SRS state is exactly
    what advance() computed → the NEXT due card is offered."""
    deps = make_deps(session, fake_llm)
    id_a, id_b = _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    deps["clock"].advance(minutes=1)  # rating happens at NOW + 1 min

    replies = await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )

    # SRS: alpha rated Good at repetitions=3 → interval 4320*2.5 = 10800
    # (steady state), status stays review, last_review_at = rating time.
    item_a = next(i for i in get_items(session) if i.id == id_a)
    now = NOW + timedelta(minutes=1)
    expected = advance(
        _engine_state_of(item_a), Quality.GOOD, now
    )
    assert item_a.repetitions == expected.repetitions == 4
    assert item_a.ease == pytest.approx(expected.ease) == 2.5
    assert item_a.interval_minutes == expected.interval_minutes == 10800
    assert item_a.next_review_at == expected.next_review_at == now + timedelta(
        minutes=10800
    )
    assert item_a.last_review_at == now
    assert item_a.status == "review"

    # The NEXT due card (beta) is offered front-only.
    assert len(replies) == 2
    assert "Next" in replies[0]["text"]
    assert "1/2" in replies[0]["text"]
    assert replies[1]["text"] == CARD_FRONT.format(front="beta")
    assert _buttons(replies[1]["markup"]) == ["Show answer", "Stop"]


def _engine_state_of(item):
    from spacedbro.srs import SRSState, SRSStatus

    return SRSState(
        front=item.front,
        back=item.back,
        context=item.context,
        repetitions=3,
        ease=2.5,
        interval_minutes=4320,
        next_review_at=item.next_review_at,
        last_review_at=item.last_review_at,
        status=SRSStatus(item.status),
    )


async def test_rate_all_qualities_apply_their_engine_mapping(
    session, fake_llm, record_answers
):
    """Again/Hard/Good/Easy each persist exactly the engine's mapping
    (design §6 via the BON-28 engine) — one fresh due card each."""
    cases = [
        ("Again", Quality.AGAIN),
        ("Hard", Quality.HARD),
        ("Easy", Quality.EASY),
    ]
    for i, (label, quality) in enumerate(cases):
        # A FRESH session per case: the in-memory DB is fresh too, so the
        # card id restarts at 1 and the due queue holds exactly one card.
        session2 = make_session()
        deps = make_deps(session2, fake_llm)
        _save_due(session2, deps["clock"], deps, f"card{i}", f"m{i}")
        deps["clock"].advance(minutes=2)
        record_answers["sent"].clear()
        await _start_session(deps, record_answers)
        await _reveal(deps, record_answers)
        await _press(
            deps, _button(record_answers["sent"][-1]["markup"], label), record_answers
        )
        item = get_items(session2)[0]
        now = NOW + timedelta(minutes=2)
        expected = advance(_engine_state_of(item), quality, now)
        assert item.interval_minutes == expected.interval_minutes, label
        assert item.repetitions == expected.repetitions, label
        assert item.ease == pytest.approx(expected.ease), label
        assert item.status == (
            expected.status.value
            if hasattr(expected.status, "value")
            else expected.status
        ), label
        assert item.next_review_at == expected.next_review_at, label
        assert item.last_review_at == now, label
        # ease-floor check for Again: 2.5 - 0.2 = 2.3 (above MIN_EASE).
        if quality is Quality.AGAIN:
            assert item.ease == pytest.approx(2.3)
            assert item.repetitions == 0 and item.interval_minutes == 10
        if quality is Quality.HARD:
            assert item.ease == pytest.approx(2.5 - HARD_EASE_PENALTY)
            assert item.interval_minutes == 5184  # max(10, int(4320*1.2))
            assert item.repetitions == 4
        if quality is Quality.EASY:
            assert item.ease == pytest.approx(2.5 + EASY_EASE_BONUS)
            assert item.interval_minutes == int(4320 * 2.5 * 1.3)
        session2.close()


async def test_good_rating_caps_at_max_interval(
    session, fake_llm, record_answers
):
    """Good on a huge interval hits the 259200 (180 day) cap (design §6
    'Max interval') — the engine caps, the repository stores the cap."""
    deps = make_deps(session, fake_llm)
    _save_due(
        session,
        deps["clock"],
        deps,
        "big",
        "big back",
        interval_minutes=MAX_INTERVAL_MINUTES,
    )
    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    item = get_items(session)[0]
    assert item.interval_minutes == MAX_INTERVAL_MINUTES


# --- After rating: stop when nothing due (design §7) ------------------------------


async def test_session_stops_when_nothing_left_due(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["only"])
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )

    assert replies[-1]["text"] == SESSION_DONE
    assert replies[-1]["markup"] is None
    assert review._session(deps["store"], TG_ID) is None  # session cleared
    # The rated card is no longer due (next_review_at far in the future).
    assert get_items(session)[0].next_review_at > NOW


async def test_aged_again_card_is_not_reoffered_same_session(
    session, fake_llm, record_answers
):
    """A card rated Again becomes due again in 10 minutes — but the
    re-read due queue must not present it AGAIN within the same session
    (the rating happened just now; the 10-minute window has not passed
    at offer time). It stays due for later, no penalty (design §7)."""
    deps = make_deps(session, fake_llm)
    id_a, id_b = _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Again"), record_answers
    )

    # alpha: repetitions 3 → 0, interval 10, due again in 10 min —
    # which is in the FUTURE relative to the rating time.
    item_a = next(i for i in get_items(session) if i.id == id_a)
    assert item_a.repetitions == 0
    assert item_a.interval_minutes == 10
    assert item_a.ease == pytest.approx(2.3)
    assert item_a.status == "learning"
    assert item_a.next_review_at == NOW + timedelta(minutes=1) + timedelta(minutes=10)

    # The session offered beta (still due), not alpha again.
    assert "Next" in replies[0]["text"]
    assert replies[1]["text"] == CARD_FRONT.format(front="beta")


async def test_after_all_rated_a_later_review_starts_fresh(
    session, fake_llm, record_answers
):
    """The due queue is re-read on every /review: after rating both
    cards Good (far-future), a later /review finds nothing due — and
    when the clock passes the next_review_at of an Again-rated card,
    that card shows up as due again (no penalty, design §7)."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Again"), record_answers
    )
    # beta is now on screen (alpha was rated Again).
    await _reveal(deps, record_answers)
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    assert review._session(deps["store"], TG_ID) is None

    # 15 minutes later: alpha (due in 10 min from its rating) is due
    # again; beta (Good → 10800 min) is not.
    deps["clock"].advance(minutes=14)
    record_answers["sent"].clear()
    replies = await _start_session(deps, record_answers)
    assert DUE_INTRO.format(count=1, plural="") in replies[0]["text"]
    assert replies[1]["text"] == CARD_FRONT.format(front="alpha")


async def test_card_becoming_due_mid_session_joins_queue(
    session, fake_llm, record_answers
):
    """design §7: "Proactive and on-demand share the same due queue" — a
    card that BECOMES due while a session is running (the due query is
    re-read after every rating) joins the queue for the next offer; the
    reported total stays what it was at session start."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha"])
    # "beta" becomes due 3 minutes from now (e.g. its 20-minute new-card
    # window from another flow).
    user_id = get_user(session).id
    deps["items"].save(
        user_id, "beta", back="b", next_review_at=NOW + timedelta(minutes=3), now=NOW
    )
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    # Only alpha is due at this point; beta is not yet.
    assert DUE_INTRO.format(count=1, plural="") in record_answers["sent"][0]["text"]
    await _reveal(deps, record_answers)

    # Time passes: beta becomes due before the rating is applied.
    deps["clock"].advance(minutes=2)
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    # After rating alpha, the re-read due queue now contains beta.
    assert "Next" in replies[0]["text"]
    assert "1/1" in replies[0]["text"]  # total as reported at session start
    assert replies[1]["text"] == CARD_FRONT.format(front="beta")


# --- Proactive counter must NOT move (design §7 / §8) -----------------------------


async def test_on_demand_review_does_not_increment_proactive_count(
    session, fake_llm, record_answers
):
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    user = get_user(session)
    assert user.proactive_count == 0
    assert user.proactive_count_date is None

    deps["clock"].advance(minutes=1)
    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    await _reveal(deps, record_answers)
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Easy"), record_answers
    )

    session.refresh(user)
    assert user.proactive_count == 0  # untouched by on-demand reviews
    assert user.proactive_count_date is None
    assert len(get_items(session)) == 2  # both cards rated once


# --- Stop: unattended cards remain due, no penalty (design §7) ----------------------


async def test_stop_keeps_all_cards_due_untouched(session, fake_llm, record_answers):
    deps = make_deps(session, fake_llm)
    id_a, id_b = _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    state_before = {
        i.id: (i.repetitions, i.ease, i.interval_minutes, i.next_review_at, i.last_review_at, i.status)
        for i in get_items(session)
    }

    await _start_session(deps, record_answers)
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Stop"), record_answers
    )

    assert replies[-1]["text"] == SESSION_STOPPED
    # The session state is gone (a fresh /review would start over)…
    assert review._session(deps["store"], TG_ID) is None
    # …but every card's SRS state is byte-for-byte unchanged — both
    # cards remain due, no penalty.
    for item in get_items(session):
        assert (
            item.repetitions,
            item.ease,
            item.interval_minutes,
            item.next_review_at,
            item.last_review_at,
            item.status,
        ) == state_before[item.id]
    assert deps["items"].due_count(get_user(session).id, NOW) == 2


async def test_stop_after_one_rating_keeps_unrated_card_due(
    session, fake_llm, record_answers
):
    """Stop after rating the first card: the rated card moves forward
    (engine applied), the UNRATED card remains due exactly as before
    (design §7: 'Unattended due cards: remain due; no extra penalty')."""
    deps = make_deps(session, fake_llm)
    id_a, id_b = _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    b_before = next(i for i in get_items(session) if i.id == id_b)
    b_state = (
        b_before.repetitions,
        b_before.ease,
        b_before.interval_minutes,
        b_before.next_review_at,
        b_before.last_review_at,
        b_before.status,
    )
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    # beta is now on screen — stop instead of rating it.
    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Stop"), record_answers
    )

    assert replies[-1]["text"] == SESSION_STOPPED
    item_b = next(i for i in get_items(session) if i.id == id_b)
    assert (
        item_b.repetitions,
        item_b.ease,
        item_b.interval_minutes,
        item_b.next_review_at,
        item_b.last_review_at,
        item_b.status,
    ) == b_state  # untouched — still due
    assert deps["items"].due_count(get_user(session).id, NOW + timedelta(minutes=1)) == 1


async def test_new_review_replaces_stale_session_state(
    session, fake_llm, record_answers
):
    """A fresh /review mid-session drops the old session: an in-flight
    rating button from the replaced session must apply NOTHING — the
    new session has the fresh due queue and the new count."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    stale_easy = _button(record_answers["sent"][-1]["markup"], "Easy")  # bound to alpha
    # Rate alpha Good: the session advances — beta is on screen.
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )

    # A new /review (the user re-triggers): only beta is still due
    # (alpha's next review is ~18h out).
    record_answers["sent"].clear()
    replies = await _start_session(deps, record_answers)
    assert DUE_INTRO.format(count=1, plural="") in replies[0]["text"]
    assert replies[1]["text"] == CARD_FRONT.format(front="beta")

    # The OLD [Easy] from alpha's reveal is now stale — the session's
    # on-screen card is beta, so nothing is applied to either card.
    beta_before = (
        get_items(session)[1].repetitions,
        get_items(session)[1].ease,
        get_items(session)[1].interval_minutes,
    )
    stale_replies = await _press(deps, stale_easy, record_answers)
    assert stale_replies[-1]["text"] == STALE_CARD
    beta_after = (
        get_items(session)[1].repetitions,
        get_items(session)[1].ease,
        get_items(session)[1].interval_minutes,
    )
    assert beta_after == beta_before  # no rating leaked through


# --- Callback idempotency (design §1) ----------------------------------------------


async def test_rating_replay_is_no_op_srs_applied_once(
    session, fake_llm, record_answers
):
    """Replaying the SAME rating callback id (double-tap / redelivery)
    must not apply the rating twice: ledger no-op, SRS unchanged after
    the first application."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    good_btn = _button(record_answers["sent"][-1]["markup"], "Good")
    data = good_btn.callback_data

    await handle_callback(
        make_callback(data=data, message=make_message(message_id=901)), **deps
    )
    after_first = get_items(session)[0]
    assert after_first.repetitions == 4  # applied once

    # Replay the exact same callback id.
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=902)), **deps
    )
    after_replay = get_items(session)[0]
    assert after_replay.repetitions == after_first.repetitions  # still 4
    assert after_replay.ease == after_first.ease
    assert after_replay.interval_minutes == after_first.interval_minutes
    assert after_replay.next_review_at == after_first.next_review_at
    assert "Already handled" in record_answers["answered"][-1]


async def test_show_replay_is_no_op_no_duplicate_reveal(
    session, fake_llm, record_answers
):
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])

    await _start_session(deps, record_answers)
    show_btn = _button(record_answers["sent"][-1]["markup"], "Show answer")
    data = show_btn.callback_data

    before = len(record_answers["sent"])
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=903)), **deps
    )
    reveals = record_answers["sent"][before:]
    assert len(reveals) == 1  # the one real reveal
    before2 = len(record_answers["sent"])
    await handle_callback(
        make_callback(data=data, message=make_message(message_id=904)), **deps
    )
    assert len(record_answers["sent"][before2:]) == 0  # replay: silent no-op
    assert "Already handled" in record_answers["answered"][-1]


async def test_stale_rate_button_cannot_rate_another_card(
    session, fake_llm, record_answers
):
    """A quality button bound to the PREVIOUS card (the session moved on)
    must never apply its rating to the card currently on screen."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha", "beta"])
    deps["clock"].advance(minutes=1)

    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)
    stale_easy = _button(record_answers["sent"][-1]["markup"], "Easy")
    # Rate alpha Good: beta comes on screen.
    await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    assert record_answers["sent"][-1]["text"] == CARD_FRONT.format(front="beta")
    state_beta_before = (
        get_items(session)[1].repetitions,
        get_items(session)[1].ease,
        get_items(session)[1].interval_minutes,
    )

    # Now tap the STALE [Easy] from alpha's reveal (pending is beta).
    stale_replies = await _press(deps, stale_easy, record_answers)
    assert stale_replies[-1]["text"] == STALE_CARD

    beta = get_items(session)[1]
    assert (beta.repetitions, beta.ease, beta.interval_minutes) == state_beta_before


async def test_callback_data_stays_under_64_bytes(session, fake_llm, record_answers):
    """design §1 64-byte limit: payloads are item ids / quality:id —
    content never travels in the button, for ANY id size."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["alpha"])
    await _start_session(deps, record_answers)
    front_markup = record_answers["sent"][-1]["markup"]
    await _reveal(deps, record_answers)
    quality_markup = record_answers["sent"][-1]["markup"]
    for m in (front_markup, quality_markup):
        for row in m.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode("utf-8")) <= 64, btn.callback_data


# --- Errors: friendly, no stack trace (design §9) ----------------------------------


async def test_rating_persistence_failure_offers_retry_no_state_change(
    session, fake_llm, record_answers
):
    """Mid-session DB failure: a short friendly line + retry; the card's
    state is untouched (advance is pure, the repo commits atomically),
    so the retry applies the rating exactly once — no double-apply."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])
    user_id = get_user(session).id
    real_update = deps["items"].update_srs

    def failing_update(*args, **kwargs):
        raise RuntimeError("disk full")

    deps["items"].update_srs = failing_update
    await _start_session(deps, record_answers)
    await _reveal(deps, record_answers)

    replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    assert replies[-1]["text"].startswith("That didn't save")
    assert _buttons(replies[-1]["markup"]) == ["Try again", "Stop"]
    # State untouched: nothing was rated.
    assert get_items(session)[0].repetitions == 3
    assert get_items(session)[0].last_review_at != NOW + timedelta(minutes=0)

    # Retry works: the rating is applied exactly once.
    deps["clock"].advance(minutes=5)
    deps["items"].update_srs = real_update
    try_replies = await _press(
        deps, _button(replies[-1]["markup"], "Try again"), record_answers
    )
    # [Try again] re-reveals the card (front + back, quality buttons).
    assert any("meaning 0" in r["text"] for r in try_replies)
    rate_replies = await _press(
        deps, _button(record_answers["sent"][-1]["markup"], "Good"), record_answers
    )
    item = get_items(session)[0]
    assert item.repetitions == 4  # applied exactly once
    assert rate_replies[-1]["text"] == SESSION_DONE  # only card, session over


async def test_review_entry_failure_falls_back_to_friendly_global_reply(
    session, fake_llm, record_answers
):
    """design §9 "Errors (user-facing)": a failure while reading the due
    queue escapes the handler to the dispatcher's global error handler,
    which MUST answer with a short friendly line — never a stack trace.
    Nothing is persisted."""
    from spacedbro.bot.app import BotApplication, GLOBAL_ERROR_REPLY

    deps = make_deps(session, fake_llm)
    deps["users"].get_or_create(TG_ID, NOW)
    real_due = deps["items"].due

    def broken_due(*args, **kwargs):
        raise RuntimeError("db lock")

    deps["items"].due = broken_due
    app = BotApplication(
        token="123456:TEST", llm_client=deps["llm_client"],
        database_url="sqlite://", clock=deps["clock"],
    )
    message = make_message(text="/review", message_id=960)
    try:
        with pytest.raises(RuntimeError):
            await handle_review(message, **deps)
    finally:
        deps["items"].due = real_due

    # The global error handler catches the failure and replies friendly.
    await app._on_error(
        ErrorEvent(
            update_id=1,
            update=Update(update_id=1, message=message),
            exception=RuntimeError("db lock"),
        )
    )
    assert record_answers["sent"][-1]["text"] == GLOBAL_ERROR_REPLY
    assert get_items(session) == []


async def test_global_error_on_callback_update_does_not_reply(
    session, fake_llm, record_answers
):
    """A failure on a callback update (no ``update.message``) must not
    try to answer a message — the global handler logs and stays silent
    (no crash, no stack trace)."""
    from spacedbro.bot.app import BotApplication

    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])
    await _start_session(deps, record_answers)
    show_btn = _button(record_answers["sent"][-1]["markup"], "Show answer")
    callback = make_callback(data=show_btn.callback_data, message=None)

    app = BotApplication(
        token="123456:TEST", llm_client=deps["llm_client"],
        database_url="sqlite://", clock=deps["clock"],
    )
    before = len(record_answers["sent"])
    await app._on_error(
        ErrorEvent(
            update_id=2,
            update=Update(update_id=2, callback_query=callback),
            exception=RuntimeError("boom"),
        )
    )
    assert len(record_answers["sent"]) == before  # silent, no crash


async def test_callback_with_missing_message_is_silent(
    session, fake_llm, record_answers
):
    """A callback whose card message is gone (deleted/expired): stop the
    spinner, say nothing, apply nothing."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])
    await _start_session(deps, record_answers)
    show_btn = _button(record_answers["sent"][-1]["markup"], "Show answer")

    from aiogram.types import User as TgUser

    orphan = CallbackQuery(
        id="orphan-1",
        from_user=TgUser(id=TG_ID, is_bot=False, first_name="T"),
        chat_instance="ci",
        message=None,
        data=show_btn.callback_data,
    )
    before = len(record_answers["sent"])
    await handle_callback(orphan, **deps)
    assert len(record_answers["sent"]) == before  # no replies
    assert get_items(session)[0].repetitions == 3  # nothing applied


async def test_malformed_review_payload_is_ignored(
    session, fake_llm, record_answers
):
    """A well-formed action with a garbage payload must not crash or
    apply anything (never a bot error)."""
    deps = make_deps(session, fake_llm)
    _seed_due_cards(session, deps["clock"], deps, ["solo"])
    await _start_session(deps, record_answers)

    from spacedbro.bot import callbacks as cb

    for data in (
        cb.make_callback_data(cb.ACTION_RATE, "good:xyz", cb.new_callback_id()),
        cb.make_callback_data(cb.ACTION_SHOW, "not-an-id", cb.new_callback_id()),
        cb.make_callback_data(cb.ACTION_STOP, "", cb.new_callback_id()),  # empty → parse rejects
    ):
        if data is None:
            continue
        await handle_callback(
            make_callback(data=data, message=make_message(message_id=905)), **deps
        )
    assert get_items(session)[0].repetitions == 3  # untouched
    assert "Traceback" not in str(record_answers["sent"])
