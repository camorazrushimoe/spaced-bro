"""State + callback protocol tests (BON-31, design §1 idempotency)."""

from __future__ import annotations

from spacedbro.bot import callbacks as cb
from spacedbro.bot.state import (
    Candidate,
    CallbackLedger,
    Confirmation,
    ContextStore,
)


class _Time:
    """Monotonic-ish clock for TTL tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- Callback protocol ---------------------------------------------------------


def test_roundtrip_action_payload_id() -> None:
    # Payloads are short opaque references (tokens / ids / codes) — never
    # card content, which would blow Telegram's 64-byte limit.
    data = cb.make_callback_data(cb.ACTION_SAVE, "abcd1234", "abc123")
    assert data == "save:abcd1234:abc123"
    assert cb.parse_callback_data(data) == ("save", "abcd1234", "abc123")


def test_parse_rejects_foreign_and_malformed() -> None:
    assert cb.parse_callback_data(None) is None
    assert cb.parse_callback_data("") is None
    assert cb.parse_callback_data("onlyonepart") is None
    assert cb.parse_callback_data("two:parts") is None
    assert cb.parse_callback_data("unknown:payload:id") is None
    assert cb.parse_callback_data(":payload:id") is None  # empty action
    assert cb.parse_callback_data("add:payload:") is None  # empty id
    assert cb.parse_callback_data("add::id") is None  # empty payload (use "-")


def test_parse_payload_may_contain_colons() -> None:
    # Even a colon-heavy foreign payload must survive parsing: the nonce is
    # always the last segment, the action the first.
    data = cb.make_callback_data(cb.ACTION_ADD, "12:30", "deadbeef00000000")
    assert data == "add:12:30:deadbeef00000000"
    assert cb.parse_callback_data(data) == ("add", "12:30", "deadbeef00000000")


def test_new_callback_id_is_unique() -> None:
    ids = {cb.new_callback_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(len(i) == 16 for i in ids)


def test_new_token_is_short_and_unique() -> None:
    # Tokens are the payload of add/save/regen buttons — they must stay
    # small (8 hex chars) or the callback data would approach the limit.
    tokens = {cb.new_token() for _ in range(1000)}
    assert len(tokens) == 1000
    assert all(len(t) == cb.TOKEN_LEN for t in tokens)


def test_all_action_buttons_stay_under_64_bytes() -> None:
    # Telegram's callback_data limit is 64 bytes. Payloads are short
    # references (8-char tokens, item ids, language codes), so the limit
    # holds for ANY word length — content never travels in the button.
    for action, payload in [
        (cb.ACTION_ADD, cb.new_token()),
        (cb.ACTION_SAVE, cb.new_token()),
        (cb.ACTION_REGEN, cb.new_token()),
        (cb.ACTION_SKIP, cb.NO_PAYLOAD),
        (cb.ACTION_BOOST, "999999999999"),  # very large item id
        (cb.ACTION_LANG, "en"),
    ]:
        data = cb.make_callback_data(action, payload, cb.new_callback_id())
        assert len(data.encode("utf-8")) <= 64, (action, len(data))


# --- CallbackLedger (design §1: replay = no-op) ---------------------------------


def test_ledger_first_seen_then_replay() -> None:
    ledger = CallbackLedger()
    assert ledger.first_seen("id-1") is True
    assert ledger.first_seen("id-1") is False
    assert ledger.first_seen("id-1") is False


def test_ledger_distinct_ids_independent() -> None:
    ledger = CallbackLedger()
    assert ledger.first_seen("a")
    assert ledger.first_seen("b")
    assert not ledger.first_seen("a")


# --- ContextStore (TTL + scoping) -----------------------------------------------


def test_store_set_get_pop() -> None:
    store = ContextStore(ttl_seconds=100, time_fn=_Time())
    store.set(1, "k", "v")
    assert store.get(1, "k") == "v"
    assert store.has(1, "k")
    assert store.pop(1, "k") == "v"
    assert store.get(1, "k") is None


def test_store_per_user_scoping() -> None:
    store = ContextStore()
    store.set(1, "k", "a")
    store.set(2, "k", "b")
    assert store.get(1, "k") == "a"
    assert store.get(2, "k") == "b"
    store.clear_user(1)
    assert store.get(1, "k") is None
    assert store.get(2, "k") == "b"


def test_store_ttl_expiry() -> None:
    time = _Time()
    store = ContextStore(ttl_seconds=60, time_fn=time)
    store.set(1, "k", "v")
    time.t = 59
    assert store.get(1, "k") == "v"
    time.t = 60
    assert store.get(1, "k") is None
    assert store.pop(1, "k") is None


def test_confirmation_and_candidate_are_immutable() -> None:
    conf = Confirmation(front="hello", back="привет", context="a word", key="k1")
    assert (conf.front, conf.back, conf.context, conf.key) == (
        "hello",
        "привет",
        "a word",
        "k1",
    )
    cand = Candidate(front="x", context=None)
    assert cand.context is None
