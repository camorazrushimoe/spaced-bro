"""Tests for the metrics module (BON-34, tasks.md §7 "Logs/metrics").

Delivers the ticket's "basic metrics (item counts, LLM call counts)":

- ``Metrics`` is the single, dependency-free metrics object owned by the
  process (the entrypoint constructs it once and injects it into the LLM
  client and the health server — the same injection style as the clock and
  the session factory).
- LLM call counts: the client increments a per-kind (``text``/``vision``)
  success counter and a per-kind, per-error-class failure counter. Error
  classes are the five ``spacedbro.llm.errors`` domain errors — classified
  by :func:`spacedbro.metrics.classify_llm_error`, so the LLM client never
  knows metric names.
- Item counts: gauges (total users, total items, due items) refreshed from
  the database on demand. A bound session factory makes gauges live;
  without one (or when the DB is unreachable) gauges hold their last known
  value and refresh never raises.
- ``render()`` emits stable, sorted Prometheus text (``spacedbro_*``
  names) — the ``/metrics`` body. Deterministic output: counters sorted by
  label set, gauges in a fixed order, so a scrape is comparable across
  runs (and the smoke checklist can grep it).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from spacedbro.clock import FrozenClock
from spacedbro.db.base import Base
from spacedbro.db.engine import create_db_engine, create_session_factory
from spacedbro.db.repositories import ItemRepository, UserRepository
from spacedbro.health import build_health_app
from spacedbro.llm.client import LLMClient, Message
from spacedbro.llm.config import LLMSettings
from spacedbro.llm.errors import (
    InvalidResponseError,
    ProviderUnavailableError,
    RateLimitError,
    TimeoutError,
    VisionNotSupportedError,
)
from spacedbro.metrics import Metrics, classify_llm_error
from tests.repo_fixtures import NOW

_T0 = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


# --- classify_llm_error --------------------------------------------------------


def test_classify_llm_error_maps_each_domain_error_to_a_stable_class() -> None:
    assert classify_llm_error(TimeoutError("boom")) == "timeout"
    assert classify_llm_error(RateLimitError("boom")) == "rate_limited"
    assert (
        classify_llm_error(ProviderUnavailableError("boom")) == "provider_unavailable"
    )
    assert classify_llm_error(InvalidResponseError("boom")) == "invalid_response"
    assert (
        classify_llm_error(VisionNotSupportedError("boom")) == "vision_unsupported"
    )


def test_classify_llm_error_unknown_llm_error_falls_back_to_error() -> None:
    from spacedbro.llm.errors import LLMError

    assert classify_llm_error(LLMError("new kind")) == "error"


# --- LLM call counting (via the real client, stub transport) --------------------


def _settings(**overrides) -> LLMSettings:
    defaults = dict(
        app_env="development",
        provider="openai_compatible",
        base_url="http://localhost:11434/v1",
        model="gemma-4-e2b-it",
        api_key="local-dev",
        timeout_seconds=30,
        max_retries=0,
    )
    defaults.update(overrides)
    return LLMSettings(**defaults)


def _ok_body() -> str:
    import json

    return json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


class _StubTransport:
    def __init__(self, status: int = 200, body: str = "") -> None:
        self.status = status
        self.body = body

    async def post(self, url, *, headers, json, timeout):  # noqa: ANN001
        return self.status, {}, self.body


def _client(transport, metrics: Metrics | None) -> LLMClient:
    return LLMClient(
        settings=_settings(),
        transport=transport,
        clock=FrozenClock(_T0),
        metrics=metrics,
    )


async def test_successful_text_call_counts_as_text_ok() -> None:
    metrics = Metrics()
    client = _client(_StubTransport(200, _ok_body()), metrics)
    await client.complete([Message(role="user", content="hi")])
    text = metrics.render()
    assert 'spacedbro_llm_calls_total{kind="text",outcome="ok"} 1' in text
    assert 'kind="vision"' not in text


async def test_successful_vision_call_counts_as_vision_ok() -> None:
    metrics = Metrics()
    client = _client(_StubTransport(200, _ok_body()), metrics)
    await client.complete_with_vision(
        [Message(role="user", content="hi")],
        image_url="http://example/x.png",
    )
    assert 'spacedbro_llm_calls_total{kind="vision",outcome="ok"} 1' in metrics.render()


async def test_failed_call_counts_the_error_class_not_ok() -> None:
    metrics = Metrics()
    client = _client(_StubTransport(429, "slow down"), metrics)
    with pytest.raises(RateLimitError):
        await client.complete([Message(role="user", content="hi")])
    text = metrics.render()
    assert 'spacedbro_llm_errors_total{kind="text",error="rate_limited"} 1' in text
    assert 'outcome="ok"' not in text


async def test_5xx_counts_as_provider_unavailable() -> None:
    metrics = Metrics()
    client = _client(_StubTransport(503, "oops"), metrics)
    with pytest.raises(ProviderUnavailableError):
        await client.complete([Message(role="user", content="hi")])
    assert (
        'spacedbro_llm_errors_total{kind="text",error="provider_unavailable"} 1'
        in metrics.render()
    )


async def test_metrics_is_optional_on_the_client() -> None:
    """A client without a metrics object still works (BON-30 contract)."""
    client = LLMClient(
        settings=_settings(),
        transport=_StubTransport(200, _ok_body()),
        clock=FrozenClock(_T0),
    )
    response = await client.complete([Message(role="user", content="hi")])
    assert response.content == "ok"


# --- Gauges (item counts) --------------------------------------------------------


def _session_factory(tmp_path) -> "callable":
    engine = create_db_engine(f"sqlite:///{tmp_path / 'metrics.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def _seed_users_and_items(session_factory, now: datetime) -> None:
    with session_factory() as session:
        users = UserRepository(session, FrozenClock(now))
        user_id = users.get_or_create(42, now=now)
        items = ItemRepository(session, FrozenClock(now))
        # Due now (saved 30 min earlier, 20-min default interval).
        items.save(user_id, "gato", back="cat", now=now - timedelta(minutes=30))
        # Not due (saved now).
        items.save(user_id, "perro", back="dog", now=now)


def test_gauges_refresh_from_the_database(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    metrics = Metrics(session_factory=factory)
    metrics.bind_clock(FrozenClock(NOW))
    _seed_users_and_items(factory, NOW)

    metrics.refresh_gauges()
    text = metrics.render()
    assert "spacedbro_users_total 1" in text
    assert "spacedbro_items_total 2" in text
    assert "spacedbro_items_due_total 1" in text


def test_gauges_without_a_session_factory_stay_zero() -> None:
    metrics = Metrics()
    metrics.refresh_gauges()  # must not raise
    text = metrics.render()
    assert "spacedbro_users_total 0" in text
    assert "spacedbro_items_total 0" in text
    assert "spacedbro_items_due_total 0" in text


def test_gauges_survive_an_unreachable_database(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'gone' / 'metrics.db'}")
    factory = create_session_factory(engine)
    metrics = Metrics(session_factory=factory)
    metrics.refresh_gauges()  # DB openable check failed once
    assert "spacedbro_items_total 0" in metrics.render()
    # A later refresh must not raise either — gauges are best-effort.
    metrics.refresh_gauges()


def test_unbound_user_row_does_not_break_gauges(tmp_path) -> None:
    """A user with no items still counts as a user (gauge correctness)."""
    factory = _session_factory(tmp_path)
    with factory() as session:
        UserRepository(session, FrozenClock(NOW)).get_or_create(7, now=NOW)
    metrics = Metrics(session_factory=factory)
    metrics.refresh_gauges()
    assert "spacedbro_users_total 1" in metrics.render()
    assert "spacedbro_items_total 0" in metrics.render()


# --- Rendering contract ----------------------------------------------------------


def test_render_is_sorted_and_stable() -> None:
    metrics = Metrics()
    metrics.record_llm_call("vision", ok=True)
    metrics.record_llm_call("text", ok=True)
    metrics.record_llm_call("text", ok=False, error=RateLimitError("x"))
    first = metrics.render()
    # Byte-identical across renders (no timestamps, no randomness).
    assert first == metrics.render()
    lines = first.splitlines()
    calls = [l for l in lines if l.startswith("spacedbro_llm_calls_total")]
    errors = [l for l in lines if l.startswith("spacedbro_llm_errors_total")]
    # Label lines inside each block are sorted (grep-stable order).
    assert calls == sorted(calls)
    assert errors == sorted(errors)
    assert calls[0].startswith('spacedbro_llm_calls_total{kind="text"')
    assert errors[0].startswith('spacedbro_llm_errors_total{kind="text"')


def test_render_includes_help_and_type_lines() -> None:
    metrics = Metrics()
    metrics.record_llm_call("text", ok=True)
    metrics.record_llm_call("text", ok=False, error=TimeoutError("x"))
    text = metrics.render()
    for name in (
        "spacedbro_users_total",
        "spacedbro_items_total",
        "spacedbro_items_due_total",
        "spacedbro_llm_calls_total",
        "spacedbro_llm_errors_total",
    ):
        assert f"# HELP {name}" in text
        assert f"# TYPE {name}" in text


def test_counter_lines_absent_when_never_observed() -> None:
    text = Metrics().render()
    assert "spacedbro_llm_calls_total{" not in text
    assert "spacedbro_llm_errors_total{" not in text


# --- /metrics endpoint on the health app -----------------------------------------


def _clock() -> FrozenClock:
    return FrozenClock(_T0)


async def test_metrics_endpoint_serves_prometheus_text(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    # Seed relative to the app clock (_T0): gato due at _T0-10min,
    # perro due at _T0+20min — exactly one due at scrape time.
    _seed_users_and_items(factory, _T0)
    metrics = Metrics(session_factory=factory)
    metrics.record_llm_call("text", ok=True)
    app = build_health_app(
        f"sqlite:///{tmp_path / 'metrics.db'}", _clock(), metrics=metrics
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/metrics")
        assert resp.status == 200
        text = await resp.text()
        assert "# HELP spacedbro_users_total" in text
        assert "spacedbro_items_total 2" in text
        assert "spacedbro_items_due_total 1" in text
        assert 'spacedbro_llm_calls_total{kind="text",outcome="ok"} 1' in text


async def test_metrics_endpoint_absent_without_metrics_object(tmp_path) -> None:
    app = build_health_app(f"sqlite:///{tmp_path / 'metrics.db'}", _clock())
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/metrics")).status == 404


async def test_healthz_still_works_with_metrics(tmp_path) -> None:
    app = build_health_app(
        f"sqlite:///{tmp_path / 'metrics.db'}", _clock(), metrics=Metrics()
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert (await resp.json())["status"] == "ok"


def test_metrics_registry_is_thread_safe_enough_for_single_loop() -> None:
    """Single-event-loop contract: no locks, but repeated increments from
    the same loop must not lose counts (dict ops are atomic enough under
    CPython; the test pins the observable behaviour)."""
    metrics = Metrics()
    for _ in range(1000):
        metrics.record_llm_call("text", ok=True)
    assert "spacedbro_llm_calls_total{kind=\"text\",outcome=\"ok\"} 1000" in metrics.render()
