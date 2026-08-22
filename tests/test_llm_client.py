"""Tests for the LLM client abstraction against a stub transport.

Spec: openspec/changes/llm-router/specs/llm-client/spec.md
- "Single client abstraction"
- "Stub-transport test seam" (normative): every contract test — including
  every error-mapping and retry path — runs against a stub transport with
  no real provider, no network, and no local model server.
- "Domain error set" (five classes)
- "Failure-to-domain-error mapping"
- "Timeout and retry policy"
- "Local vision failure outcome"
- "Structured output failure"

The clock is injectable (FrozenClock) so timeout/backoff boundary tests are
deterministic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pytest

from spacedbro.clock import FrozenClock
from spacedbro.llm.client import (
    LLMClient,
    LLMResponse,
    Message,
    ResponseFormat,
    Transport,
    build_llm_client,
)
from spacedbro.llm.config import LLMSettings, load_llm_settings
from spacedbro.llm.errors import (
    InvalidResponseError,
    LLMError,
    ProviderUnavailableError,
    RateLimitError,
    TimeoutError,
    VisionNotSupportedError,
)

_T0 = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


# --- Stub transport -----------------------------------------------------------


@dataclass
class StubResponse:
    #: HTTP status; 0 when the call raises/ hangs instead of returning.
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: str = "{}"
    #: raise this exception instead of returning (transport-level failure)
    exc: Exception | None = None
    #: simulate the request hanging past the client-side deadline
    hang: bool = False


@dataclass
class StubTransport:
    """Records requests; plays back a scripted sequence of responses."""

    responses: list[StubResponse]
    calls: list[dict] = field(default_factory=list)

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict,
        timeout: float,
    ) -> tuple[int, dict[str, str], str]:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if not self.responses:
            raise AssertionError("stub transport received more calls than scripted")
        response = self.responses.pop(0)
        if response.exc is not None:
            raise response.exc
        return response.status, response.headers, response.body


class ConnectionRefused(Exception):
    """Stand-in for a connection-level transport failure (refused/DNS)."""


# --- Helpers -------------------------------------------------------------------


def make_settings(**overrides) -> LLMSettings:
    defaults = dict(
        app_env="development",
        provider="openai_compatible",
        base_url="http://localhost:11434/v1",
        model="gemma-4-e2b-it",
        api_key="local-dev",
        timeout_seconds=30,
        max_retries=2,
    )
    defaults.update(overrides)
    return LLMSettings(**defaults)


def make_client(
    transport: Transport,
    *,
    clock: FrozenClock | None = None,
    **settings_kwargs,
) -> LLMClient:
    return LLMClient(
        settings=make_settings(**settings_kwargs),
        transport=transport,
        clock=clock or FrozenClock(_T0),
    )


def ok_body(content: str) -> str:
    return json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
    )


CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["candidates"],
}


def _messages() -> list[Message]:
    return [Message(role="user", content="hello")]


# --- Successful text completion via stubbed transport ---------------------------


async def test_successful_text_completion_returns_llm_response() -> None:
    transport = StubTransport([StubResponse(200, body=ok_body("gemma says hi"))])
    client = make_client(transport)

    response = await client.complete(_messages())

    assert isinstance(response, LLMResponse)
    assert response.content == "gemma says hi"
    assert transport.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer local-dev"
    assert transport.calls[0]["json"]["model"] == "gemma-4-e2b-it"
    assert transport.calls[0]["timeout"] == 30


async def test_complete_forwards_messages_and_parameters() -> None:
    transport = StubTransport([StubResponse(200, body=ok_body("ok"))])
    client = make_client(transport)

    await client.complete(
        [Message(role="system", content="be terse"), Message(role="user", content="x")],
        temperature=0.1,
        max_tokens=16,
    )

    body = transport.calls[0]["json"]
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "x"},
    ]
    assert body["temperature"] == 0.1
    assert body["max_tokens"] == 16


# --- Domain error set: exactly five classes, all LLMError -----------------------


def test_domain_error_set_is_exactly_five_llm_error_classes() -> None:
    classes = (
        TimeoutError,
        RateLimitError,
        InvalidResponseError,
        ProviderUnavailableError,
        VisionNotSupportedError,
    )
    names = {cls.__name__ for cls in classes}
    assert names == {
        "TimeoutError",
        "RateLimitError",
        "InvalidResponseError",
        "ProviderUnavailableError",
        "VisionNotSupportedError",
    }
    for cls in classes:
        assert issubclass(cls, LLMError)
        # Distinct classes: the caller can catch exactly one.
        assert len({cls.__name__ for cls in classes}) == len(classes)
    # Each carries a human-readable detail.
    err = RateLimitError(detail="HTTP 429")
    assert err.detail == "HTTP 429"
    assert "429" in str(err)


# --- Failure-to-domain-error mapping (LLM_MAX_RETRIES=0: exactly one attempt) ----


@pytest.mark.parametrize(
    ("script", "error_cls"),
    [
        (StubResponse(429, body="slow down"), RateLimitError),
        (StubResponse(exc=ConnectionRefused("refused")), ProviderUnavailableError),
        (StubResponse(401, body="unauthorized"), InvalidResponseError),
        (StubResponse(403, body="forbidden"), InvalidResponseError),
        (StubResponse(400, body="bad request"), InvalidResponseError),
        (StubResponse(404, body="model not found"), InvalidResponseError),
        (StubResponse(500, body="boom"), ProviderUnavailableError),
        (StubResponse(503, body="unavailable"), ProviderUnavailableError),
        (StubResponse(200, body=json.dumps({"id": "x"})), InvalidResponseError),  # missing choices
        (StubResponse(200, body=json.dumps({"choices": "nope"})), InvalidResponseError),
    ],
)
async def test_failure_maps_to_exactly_one_domain_error(
    script: StubResponse, error_cls: type[LLMError]
) -> None:
    transport = StubTransport([script])
    client = make_client(transport, max_retries=0)

    with pytest.raises(error_cls) as excinfo:
        await client.complete(_messages())

    assert len(transport.calls) == 1
    # Human-readable detail is present for diagnosis.
    assert excinfo.value.detail
    # No provider-specific type escapes: the exception is a domain error.
    assert type(excinfo.value) is error_cls


async def test_no_provider_type_escapes_on_transport_exception() -> None:
    transport = StubTransport([StubResponse(exc=ConnectionRefused("dns failure"))])
    client = make_client(transport, max_retries=0)

    with pytest.raises(ProviderUnavailableError):
        await client.complete(_messages())


# --- Client-side timeout ---------------------------------------------------------


async def test_timeout_raises_timeout_error_with_deadline_detail() -> None:
    # The deadline is enforced by the client from LLM_TIMEOUT_SECONDS; the
    # transport never returns. The injectable clock makes this deterministic:
    # the client waits on clock.sleep until the deadline, then maps to timeout.
    import asyncio

    class HangingTransport:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def post(self, url, *, headers, json, timeout):
            self.calls.append({"url": url, "timeout": timeout})
            await asyncio.sleep(3600)  # longer than any test deadline

    transport = HangingTransport()
    clock = FrozenClock(_T0)
    client = make_client(transport, timeout_seconds=30, max_retries=0, clock=clock)

    with pytest.raises(TimeoutError) as excinfo:
        await client.complete(_messages())

    assert excinfo.value.detail
    assert len(transport.calls) == 1
    assert transport.calls[0]["timeout"] == 30
    # The clock recorded the wait up to the deadline.
    assert clock.waits and pytest.approx(clock.total_waited(), abs=1e-9) == pytest.approx(30)


# --- Timeout & retry policy --------------------------------------------------------


async def test_retryable_errors_are_retried_then_succeed() -> None:
    transport = StubTransport(
        [
            StubResponse(429, body="slow down"),
            StubResponse(429, body="slow down"),
            StubResponse(200, body=ok_body("made it")),
        ]
    )
    clock = FrozenClock(_T0)
    client = make_client(transport, max_retries=2, clock=clock)

    response = await client.complete(_messages())

    assert response.content == "made it"
    assert len(transport.calls) == 3  # exactly 3 total attempts
    # Exactly 2 backoff waits, each within [0, min(10, 1 * 2^attempt)].
    assert len(clock.waits) == 2
    assert 0 <= clock.waits[0] <= 1  # attempt 0: cap 1 * 2^0 = 1s
    assert 0 <= clock.waits[1] <= 2  # attempt 1: cap 1 * 2^1 = 2s


async def test_backoff_respects_cap_at_higher_attempts(monkeypatch) -> None:
    import spacedbro.llm.client as llm_client_module

    delays = []

    def fake_jitter(delay: float) -> float:
        delays.append(delay)
        return delay  # degenerate "jitter" = the cap itself (worst case)

    monkeypatch.setattr(llm_client_module, "_full_jitter_delay", fake_jitter)

    transport = StubTransport(
        [StubResponse(500, body="boom")] * 2 + [StubResponse(200, body=ok_body("ok"))]
    )
    clock = FrozenClock(_T0)
    # max_retries=2 -> attempts 0,1; caps min(10, 1*2^0)=1, min(10, 1*2^1)=2
    client = make_client(transport, max_retries=2, clock=clock)

    await client.complete(_messages())

    assert delays == [1.0, 2.0]
    assert clock.waits == [1.0, 2.0]


def test_full_jitter_boundaries() -> None:
    import random
    import spacedbro.llm.client as llm_client_module

    for attempt, cap in ((0, 1.0), (1, 2.0), (2, 4.0), (3, 8.0), (4, 10.0), (9, 10.0)):
        for _ in range(50):
            delay = llm_client_module._full_jitter_delay(
                llm_client_module._BACKOFF_BASE_SECONDS * (2**attempt),
                random,
            )
            assert 0 <= delay <= cap


async def test_retry_after_header_replaces_computed_backoff() -> None:
    transport = StubTransport(
        [
            StubResponse(429, headers={"Retry-After": "7"}, body="slow down"),
            StubResponse(200, body=ok_body("ok")),
        ]
    )
    clock = FrozenClock(_T0)
    client = make_client(transport, max_retries=1, clock=clock)

    await client.complete(_messages())

    assert len(clock.waits) == 1
    assert clock.waits[0] == 7  # Retry-After used instead of the computed backoff


async def test_retryable_errors_are_exhausted_and_raise_last_error() -> None:
    transport = StubTransport([StubResponse(500, body="boom")] * 3)
    client = make_client(transport, max_retries=2)

    with pytest.raises(ProviderUnavailableError):
        await client.complete(_messages())

    assert len(transport.calls) == 3  # attempted exactly 3 times


async def test_non_retryable_errors_are_not_retried() -> None:
    transport = StubTransport([StubResponse(401, body="unauthorized")] * 3)
    client = make_client(transport, max_retries=2)

    with pytest.raises(InvalidResponseError):
        await client.complete(_messages())

    assert len(transport.calls) == 1  # attempted exactly once


async def test_max_retries_zero_means_no_retries() -> None:
    transport = StubTransport([StubResponse(500, body="boom")] * 3)
    client = make_client(transport, max_retries=0)

    with pytest.raises(ProviderUnavailableError):
        await client.complete(_messages())

    assert len(transport.calls) == 1


async def test_connection_level_failure_is_not_retried() -> None:
    transport = StubTransport([StubResponse(exc=ConnectionRefused("refused"))] * 3)
    client = make_client(transport, max_retries=2)

    with pytest.raises(ProviderUnavailableError):
        await client.complete(_messages())

    assert len(transport.calls) == 1


# --- Local vision failure outcome ----------------------------------------------------


async def test_vision_400_from_local_endpoint_is_vision_not_supported() -> None:
    transport = StubTransport(
        [
            StubResponse(
                400,
                body=json.dumps({"error": {"message": "model does not support vision"}}),
            )
        ]
    )
    client = make_client(transport, max_retries=2)  # never retried

    with pytest.raises(VisionNotSupportedError) as excinfo:
        await client.complete_with_vision(_messages(), image_url="http://img/x.png")

    assert len(transport.calls) == 1
    assert excinfo.value.detail
    # A caller catching ONLY VisionNotSupportedError handles it without
    # catching any other domain error: it is not a subclass of the others.
    assert not isinstance(
        excinfo.value,
        (ProviderUnavailableError, InvalidResponseError, RateLimitError, TimeoutError),
    )


async def test_vision_404_from_local_endpoint_is_vision_not_supported() -> None:
    transport = StubTransport([StubResponse(404, body="no such model")])
    client = make_client(transport)
    with pytest.raises(VisionNotSupportedError):
        await client.complete_with_vision(_messages(), image_url="http://img/x.png")


async def test_vision_unreachable_local_endpoint_is_provider_unavailable() -> None:
    # No distinguishing signal -> conservative default, treated as transient.
    transport = StubTransport([StubResponse(exc=ConnectionRefused("refused"))])
    client = make_client(transport, max_retries=0)

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await client.complete_with_vision(_messages(), image_url="http://img/x.png")

    assert not isinstance(excinfo.value, VisionNotSupportedError)


async def test_vision_5xx_is_provider_unavailable_not_vision_not_supported() -> None:
    transport = StubTransport([StubResponse(503, body="overloaded")])
    client = make_client(transport, max_retries=0)

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await client.complete_with_vision(_messages(), image_url="http://img/x.png")

    assert not isinstance(excinfo.value, VisionNotSupportedError)


async def test_vision_success_sends_image_part_and_parses_like_text() -> None:
    transport = StubTransport([StubResponse(200, body=ok_body("a word: ubiquitous"))])
    client = make_client(transport)

    response = await client.complete_with_vision(_messages(), image_url="http://img/x.png")

    assert response.content == "a word: ubiquitous"
    body = transport.calls[0]["json"]
    user_part = body["messages"][0]["content"]
    # The message content becomes a list of parts: image_url + text.
    assert isinstance(user_part, list)
    kinds = {part["type"] for part in user_part}
    assert kinds == {"image_url", "text"}
    assert user_part[0]["image_url"]["url"] == "http://img/x.png"


# --- Structured output -----------------------------------------------------------------


async def test_structured_output_well_formed_json_parses_into_response() -> None:
    content = json.dumps({"candidates": ["ubiquitous"]})
    transport = StubTransport([StubResponse(200, body=ok_body(content))])
    client = make_client(transport)

    response = await client.complete(
        _messages(), response_format=ResponseFormat(schema=CANDIDATES_SCHEMA)
    )

    assert response.structured == {"candidates": ["ubiquitous"]}
    body = transport.calls[0]["json"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == CANDIDATES_SCHEMA


async def test_structured_output_malformed_json_raises_without_retry() -> None:
    transport = StubTransport([StubResponse(200, body=ok_body("{not json"))])
    client = make_client(transport, max_retries=2)

    with pytest.raises(InvalidResponseError) as excinfo:
        await client.complete(
            _messages(), response_format=ResponseFormat(schema=CANDIDATES_SCHEMA)
        )

    assert len(transport.calls) == 1  # no retry
    # detail preserves the raw (truncated) body for diagnosis.
    assert "{not json" in excinfo.value.detail


async def test_structured_output_schema_mismatch_raises_invalid_response() -> None:
    content = json.dumps({"unrelated": True})  # required field `candidates` missing
    transport = StubTransport([StubResponse(200, body=ok_body(content))])
    client = make_client(transport, max_retries=2)

    with pytest.raises(InvalidResponseError):
        await client.complete(
            _messages(), response_format=ResponseFormat(schema=CANDIDATES_SCHEMA)
        )

    assert len(transport.calls) == 1


async def test_structured_output_wrong_type_raises_invalid_response() -> None:
    content = json.dumps({"candidates": "not-a-list"})
    transport = StubTransport([StubResponse(200, body=ok_body(content))])
    client = make_client(transport, max_retries=2)

    with pytest.raises(InvalidResponseError):
        await client.complete(
            _messages(), response_format=ResponseFormat(schema=CANDIDATES_SCHEMA)
        )


# --- Observability -----------------------------------------------------------------


async def test_per_call_usage_and_latency_logged_at_debug(caplog) -> None:
    import logging as _logging

    transport = StubTransport([StubResponse(200, body=ok_body("ok"))])
    client = make_client(transport)

    with caplog.at_level(_logging.DEBUG):
        await client.complete(_messages())

    debugs = [r for r in caplog.records if r.levelno == _logging.DEBUG]
    assert any("usage" in r.message and "prompt_tokens" in r.message for r in debugs)
    assert any("latency" in r.message for r in debugs)


async def test_full_prompt_response_logging_off_by_default(caplog) -> None:
    import logging as _logging

    transport = StubTransport([StubResponse(200, body=ok_body("ok"))])
    client = make_client(transport)

    with caplog.at_level(_logging.DEBUG):
        await client.complete(_messages())

    assert not any("prompt=" in r.message for r in caplog.records)


async def test_full_prompt_response_logging_on_flag(monkeypatch, caplog) -> None:
    import logging as _logging

    monkeypatch.setenv("LLM_LOG_PROMPTS", "1")
    transport = StubTransport([StubResponse(200, body=ok_body("ok"))])
    client = make_client(transport)  # flag read at construction

    with caplog.at_level(_logging.DEBUG):
        await client.complete(_messages())

    assert any("prompt=" in r.message and "response=" in r.message for r in caplog.records)


# --- Behaviour across environments ---------------------------------------------------------


async def test_same_code_path_for_development_and_production_resolved_clients() -> None:
    dev = load_llm_settings({"APP_ENV": "development"})
    prod = load_llm_settings({"APP_ENV": "production", "OPENAI_API_KEY": "sk-x"})

    dev_transport = StubTransport([StubResponse(200, body=ok_body("dev answer"))])
    prod_transport = StubTransport([StubResponse(200, body=ok_body("prod answer"))])

    dev_client = build_llm_client(dev, dev_transport, FrozenClock(_T0))
    prod_client = build_llm_client(prod, prod_transport, FrozenClock(_T0))

    dev_response = await dev_client.complete(_messages())
    prod_response = await prod_client.complete(_messages())

    assert dev_response.content == "dev answer"
    assert prod_response.content == "prod answer"
    # Only the resolved configuration differs.
    assert dev_transport.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"
    assert prod_transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert prod_transport.calls[0]["headers"]["Authorization"] == "Bearer sk-x"


async def test_vision_in_production_uses_same_contract() -> None:
    settings = make_settings(
        app_env="production",
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-luna",
        api_key="sk-x",
    )
    transport = StubTransport([StubResponse(200, body=ok_body("prod vision answer"))])
    client = make_client(transport, **asdict(settings))

    response = await client.complete_with_vision(_messages(), image_url="http://img/x.png")

    assert isinstance(response, LLMResponse)
    assert response.content == "prod vision answer"
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
