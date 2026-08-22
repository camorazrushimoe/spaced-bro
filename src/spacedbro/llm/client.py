"""The LLM client — the single door to any LLM provider.

Spec: ``openspec/changes/llm-router/`` —
- specs/llm-client/spec.md: "Single client abstraction",
  "Stub-transport test seam" (normative), "Domain error set",
  "Failure-to-domain-error mapping", "Timeout and retry policy",
  "Local vision failure outcome", "Structured output failure",
  "Behaviour across environments".
- design.md: "Client interface (contract)", "Failure-to-domain-error
  mapping", "Timeout & retry policy (pinned)".

Contract in one paragraph: ``LLMClient`` is a single abstraction with
``complete`` (text) and ``complete_with_vision`` (image + text), each with
optional structured output. One client serves both backends
(``openai_compatible`` local endpoint, ``openai``) — which backend is active
is a property of the resolved configuration (``LLMSettings``), never of the
calling code. The client talks to the provider through an injected
**transport** (the only network seam) and an injected **clock** (the only
time seam), so every contract test — error mapping, retry, backoff,
timeout — runs against a stub transport with no network, no real provider,
and no local model server. Only the five domain errors from
``spacedbro.llm.errors`` escape; provider-specific types never do.

Pinned retry policy (design.md): per-attempt client-side deadline
``LLM_TIMEOUT_SECONDS`` (default 30); ``LLM_MAX_RETRIES`` default 2;
exponential backoff with full jitter, base 1s, cap 10s; ``Retry-After`` on a
rate-limited response replaces the computed delay for that retry. Retryable:
``timeout``, ``rate_limit``, ``provider_unavailable`` caused by 5xx. Never
retried: ``invalid_response``, ``vision_not_supported``, connection-level
``provider_unavailable``. On exhaustion the last error is raised unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random as _random_module
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable

from spacedbro.clock import Clock, UtcClock
from spacedbro.llm.config import LLMSettings
from spacedbro.llm.errors import (
    LLMError,
    InvalidResponseError,
    ProviderUnavailableError,
    RateLimitError,
    TimeoutError,
    VisionNotSupportedError,
)

logger = logging.getLogger(__name__)

#: Backoff: exponential with full jitter, delay in [0, min(cap, base * 2^attempt)].
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 10.0

#: Raw-body truncation for human-readable error details.
_MAX_DETAIL_BODY = 500

#: HTTP status codes that distinguish a local vision capability gap from a
#: transient outage (design.md — "Failure-to-domain-error mapping").
_VISION_UNSUPPORTED_STATUSES = frozenset({400, 404, 422})


def _truncate(text: str, limit: int = _MAX_DETAIL_BODY) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"… (+{len(text) - limit} more chars)"


def _full_jitter_delay(cap: float, random: Any = _random_module) -> float:
    """Full jitter: a uniform delay in ``[0, min(_BACKOFF_CAP_SECONDS, cap)]``.

    The caller passes the unclamped ``base * 2^attempt``; the 10s cap is
    applied here (the pinned policy). ``random`` is injectable (defaults to
    :mod:`random`) so boundary tests can pin or seed it.
    """
    return random.uniform(0.0, min(_BACKOFF_CAP_SECONDS, cap))


@runtime_checkable
class Transport(Protocol):
    """The minimal request/response seam the client talks through.

    The production implementation wraps ``aiohttp``; contract tests inject
    a stub. The returned body is the response text (decoded). Any transport
    failure (connection refused, DNS, network unreachable, read error) must
    surface as a raised exception — it never maps to a status code.
    """

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> tuple[int, dict[str, str], str]:
        """Send one request; return ``(status, headers, body_text)``."""
        ...


@dataclass(frozen=True, slots=True)
class Message:
    """A chat message in the OpenAI chat-completions shape.

    ``content`` is a plain string for text messages. (Vision image parts are
    attached by ``complete_with_vision``; callers pass text messages.)
    """

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ResponseFormat:
    """Structured-output request: the provider must answer with a JSON
    object matching ``schema`` (a JSON-Schema object)."""

    schema: Mapping[str, Any]


@dataclass(slots=True)
class LLMResponse:
    """The parsed result of one completion — provider-agnostic.

    ``structured`` is ``None`` unless structured output was requested, in
    which case it holds the parsed (and schema-validated) JSON object.
    """

    content: str
    model: str
    finish_reason: str
    structured: Any | None = None
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    """The single abstraction application code depends on — and, as the
    spec's contract sketch prescribes (an ``LLMClient`` class with
    ``complete`` / ``complete_with_vision``), the concrete client that
    serves both backends.

    Handlers receive an ``LLMClient`` by construction (dependency
    injection) and never instantiate a provider client themselves. The
    interface exposes no provider-specific types. Which backend is active
    is decided entirely by the resolved ``LLMSettings`` (``openai`` vs
    ``openai_compatible``), so the same calling code runs against local
    Gemma and against OpenAI unchanged.

    Construction takes the resolved settings plus the two injectable seams
    (``transport``, ``clock``); contract tests pass a stub transport and a
    frozen clock, so no network, no real provider, and no local model
    server are ever needed.
    """

    _settings: LLMSettings
    _transport: Transport
    _clock: Clock
    _random: Any
    _url: str
    _headers: dict[str, str]
    _log_prompts: bool

    def __init__(
        self,
        *,
        settings: LLMSettings,
        transport: Transport | None = None,
        clock: Clock | None = None,
        random: Any = _random_module,
    ) -> None:
        self._settings = settings
        self._transport: Transport = transport if transport is not None else OpenAIChatTransport()
        self._clock: Clock = clock if clock is not None else UtcClock()
        self._random = random
        self._url = settings.base_url.rstrip("/") + "/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }
        # Full prompt/response logging is OFF by default and enabled only by
        # the explicit flag LLM_LOG_PROMPTS=1 (design.md — Observability); it
        # is never enabled implicitly. (Not a config variable — the LLM
        # configuration surface is closed per the spec.)
        self._log_prompts = os.environ.get("LLM_LOG_PROMPTS", "") == "1"

    # --- Public contract ---------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        *,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Text completion with optional structured output."""
        wire_messages = [{"role": m.role, "content": m.content} for m in messages]
        body = self._build_body(
            self._settings.model, wire_messages, response_format, temperature, max_tokens
        )
        parsed = await self._attempt_with_retry(body)
        return self._to_response(parsed, response_format)

    async def complete_with_vision(
        self,
        messages: list[Message],
        *,
        image_url: str,
        image_data_url: str | None = None,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Vision completion (image + text) with optional structured output.

        The image is attached to every text message as a list of parts
        (``image_url`` + ``text``), the OpenAI multimodal content shape.
        ``image_data_url`` (an ``data:`` URL) is preferred when the image is
        held in memory; otherwise ``image_url`` is sent as-is.
        """
        content = image_data_url if image_data_url is not None else image_url
        wire_messages: list[dict[str, Any]] = [
            {
                "role": message.role,
                "content": [
                    {"type": "image_url", "image_url": {"url": content}},
                    {"type": "text", "text": message.content},
                ],
            }
            for message in messages
        ]
        body = self._build_body(
            self._settings.model, wire_messages, response_format, temperature, max_tokens
        )
        parsed = await self._attempt_with_retry(body, vision=True)
        return self._to_response(parsed, response_format)

    # --- Retry loop -----------------------------------------------------------

    async def _attempt_with_retry(
        self, body: dict[str, Any], *, vision: bool = False
    ) -> dict[str, Any]:
        """Run one attempt, retrying only the retryable set.

        Raises the last domain error unchanged on exhaustion. Each attempt
        gets a fresh per-attempt deadline (``LLM_TIMEOUT_SECONDS``).
        """
        deadline = self._settings.timeout_seconds
        max_retries = self._settings.max_retries
        attempt = 0
        while True:
            attempt_started = self._clock.utc_now()
            try:
                status, headers, raw = await self._post_with_deadline(
                    body, deadline, attempt_started
                )
            except TimeoutError:
                # Client-side deadline — retryable.
                if attempt >= max_retries:
                    raise
                delay = self._jittered_delay(attempt)
                logger.debug("LLM attempt %d timed out; retrying in %.2fs", attempt, delay)
                await self._clock.sleep(delay)
                attempt += 1
                continue

            error = self._map_http(status, headers, raw, vision=vision)
            if error is None:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    raise InvalidResponseError(
                        f"HTTP 200 body is not valid JSON: {_truncate(raw)}"
                    ) from None
                self._validate_chat_completion_shape(parsed)
                self._log_success(parsed, raw, body, attempt, attempt_started)
                return parsed

            if not self._is_retryable(error) or attempt >= max_retries:
                raise error

            delay = self._retry_delay_for(error, headers, attempt)
            logger.debug(
                "LLM attempt %d failed (%s); retrying in %.2fs",
                attempt,
                type(error).__name__,
                delay,
            )
            await self._clock.sleep(delay)
            attempt += 1

    async def _post_with_deadline(
        self, body: dict[str, Any], deadline: float, attempt_started: datetime
    ) -> tuple[int, dict[str, str], str]:
        """One transport call under the client-side deadline.

        The deadline is client-side: the transport call is raced against
        the deadline measured on the **injected clock** — deterministic
        under a frozen test clock — and the transport is additionally told
        the per-request ``timeout``. Whichever fires first maps to the
        ``timeout`` domain error.
        """
        remaining = max(
            0.0, deadline - (self._clock.utc_now() - attempt_started).total_seconds()
        )
        if remaining <= 0:
            raise TimeoutError(
                f"client-side deadline of {deadline:.0f}s "
                f"(LLM_TIMEOUT_SECONDS) expired with no response"
            )
        post_task = asyncio.ensure_future(
            self._transport.post(
                self._url,
                headers=self._headers,
                json=body,
                timeout=deadline,
            )
        )
        try:
            await asyncio.sleep(0)  # let the post task make its first progress
            if post_task.done():
                return post_task.result()  # re-raises transport exceptions
            deadline_task = asyncio.ensure_future(self._clock.sleep(remaining))
            try:
                done, _ = await asyncio.wait(
                    {post_task, deadline_task}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in (post_task, deadline_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(post_task, deadline_task, return_exceptions=True)
            if post_task in done:
                return post_task.result()
            raise asyncio.TimeoutError()
        except (asyncio.TimeoutError, TimeoutError):
            raise TimeoutError(
                f"client-side deadline of {deadline:.0f}s "
                f"(LLM_TIMEOUT_SECONDS) expired with no response"
            ) from None
        except LLMError:
            # A domain error that already escaped the transport passes
            # through unchanged — "raise the last domain error unchanged".
            raise
        except Exception as exc:  # transport-level: refused / DNS / unreachable / read
            # Connection-level provider_unavailable — never retried.
            raise ProviderUnavailableError(
                f"provider unreachable: {type(exc).__name__}: {exc}"
            ) from None

    @staticmethod
    def _validate_chat_completion_shape(parsed: Any) -> None:
        """Structurally valid chat-completions body: a JSON object with a
        non-empty ``choices`` list whose first entry carries
        ``message.content`` as a string.

        Otherwise the row "HTTP 200, body missing ``choices``" of the
        mapping table applies: ``invalid_response``.
        """
        if not isinstance(parsed, dict):
            raise InvalidResponseError("HTTP 200 chat-completions body is not a JSON object")
        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            raise InvalidResponseError("HTTP 200 chat-completions body missing 'choices'")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise InvalidResponseError(
                "HTTP 200 chat-completions body has a malformed 'choices[0].message.content'"
            )

    def _log_success(
        self,
        parsed: dict[str, Any],
        raw: str,
        body: dict[str, Any],
        attempt: int,
        attempt_started: datetime,
    ) -> None:
        """Per-call observability (design.md — Observability).

        Token usage and latency are logged at DEBUG on every successful
        call. Full prompt/response logging only when the explicit
        ``LLM_LOG_PROMPTS=1`` flag is set (never implicitly).
        """
        usage = parsed.get("usage")
        latency = (self._clock.utc_now() - attempt_started).total_seconds()
        logger.debug(
            "LLM call ok: model=%s attempts=%d latency=%.3fs usage=%s",
            parsed.get("model", self._settings.model),
            attempt + 1,
            latency,
            usage if isinstance(usage, dict) else {},
        )
        if self._log_prompts:
            logger.debug(
                "LLM prompt=%s response=%s", _truncate(json.dumps(body)), _truncate(raw)
            )

    # --- Failure mapping ---------------------------------------------------------

    def _map_http(
        self,
        status: int,
        headers: dict[str, str],
        raw: str,
        *,
        vision: bool,
    ) -> Exception | None:
        """Map one HTTP outcome to a domain error, or ``None`` when the
        response is a usable 2xx (the raw body is then parsed by the caller).

        Follows the spec's failure-to-domain-error table row by row. The
        local-vision carve-out (400/404/422 → ``vision_not_supported``)
        applies only to vision requests against a local (non-OpenAI)
        endpoint; everything else falls through to the conservative
        ``provider_unavailable`` default.
        """
        if 200 <= status < 300:
            return None

        body = _truncate(raw)
        detail = f"HTTP {status}: {body}"
        if status == 429:
            return RateLimitError(detail)
        if status in (401, 403, 400, 404):
            if (
                vision
                and self._settings.provider != "openai"
                and status in _VISION_UNSUPPORTED_STATUSES
            ):
                return VisionNotSupportedError(detail)
            return InvalidResponseError(detail)
        if 500 <= status <= 599:
            return ProviderUnavailableError(detail, retryable=True)
        # Anything else: conservative default, treated as transient.
        return ProviderUnavailableError(detail, retryable=False)

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """Pinned retry set: ``timeout``, ``rate_limit``,
        ``provider_unavailable`` caused by 5xx.

        Never retried: ``invalid_response``, ``vision_not_supported``,
        connection-level ``provider_unavailable``.
        """
        if isinstance(error, (TimeoutError, RateLimitError)):
            return True
        return isinstance(error, ProviderUnavailableError) and error.retryable

    def _retry_delay_for(
        self, error: Exception, headers: dict[str, str], attempt: int
    ) -> float:
        """Delay before retry ``attempt`` (zero-based retry index).

        A ``Retry-After`` header on a rate-limited response replaces the
        computed backoff for that retry.
        """
        if isinstance(error, RateLimitError):
            retry_after = headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return max(0.0, float(retry_after.strip()))
                except ValueError:
                    pass  # non-numeric Retry-After: fall through to backoff
        return self._jittered_delay(attempt)

    def _jittered_delay(self, attempt: int) -> float:
        # ``attempt`` = zero-based retry index; the 10s cap is applied
        # inside _full_jitter_delay (pinned policy: base=1s, cap=10s).
        # The *injected* random is consulted so backoff is deterministic
        # under a seeded/pinned ``random`` seam (tests).
        return _full_jitter_delay(_BACKOFF_BASE_SECONDS * (2**attempt), self._random)

    # --- Request / response shapes ------------------------------------------------

    @staticmethod
    def _build_body(
        model: str,
        wire_messages: list[dict[str, Any]],
        response_format: ResponseFormat | None,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": wire_messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if response_format is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": dict(response_format.schema),
                },
            }
        return body

    def _to_response(
        self, parsed: dict[str, Any], response_format: ResponseFormat | None
    ) -> LLMResponse:
        structured = None
        if response_format is not None:
            structured = self._parse_structured(parsed, response_format)
        choices = parsed["choices"]
        choice = choices[0]
        usage = parsed.get("usage")
        return LLMResponse(
            content=choice["message"]["content"],
            model=parsed.get("model", self._settings.model),
            finish_reason=choice.get("finish_reason", "stop"),
            structured=structured,
            usage=usage if isinstance(usage, dict) else {},
        )

    def _parse_structured(
        self, parsed: dict[str, Any], response_format: ResponseFormat
    ) -> Any:
        """Parse and schema-check the structured payload.

        Malformed JSON or a schema mismatch (required field missing / wrong
        type) raises ``invalid_response``; the detail preserves the raw,
        truncated content for diagnosis (spec: "Structured output failure").
        """
        try:
            content = parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise InvalidResponseError(
                "structured output: response missing message content"
            ) from None
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidResponseError(
                f"structured output is not valid JSON: {_truncate(str(content))} ({exc})"
            ) from None
        _validate_against_schema(data, dict(response_format.schema))
        return data


class OpenAIChatTransport:
    """Production transport over the OpenAI chat-completions HTTP API.

    Both backends speak this one dialect, so a single client handles
    ``openai`` and ``openai_compatible`` — only ``base_url``/``api_key``
    from the resolved settings differ. ``aiohttp`` is imported lazily so
    the module (and every stub-transport test) stays importable without
    touching the network stack.
    """

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> tuple[int, dict[str, str], str]:
        import aiohttp

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.post(url, headers=headers, json=json) as response:
                body = await response.text()
                return response.status, dict(response.headers), body


def _validate_against_schema(data: Any, schema: Mapping[str, Any]) -> None:
    """Validate ``data`` against the (small) JSON-Schema subset used for
    structured output: ``type``, ``properties``, ``required``, ``items``,
    ``enum``.

    Raises ``InvalidResponseError`` naming the first violation.
    """
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(data, expected_type):
        raise InvalidResponseError(
            f"structured output failed schema: expected type {expected_type!r}, "
            f"got {type(data).__name__}: {_truncate(str(data))}"
        )
    if "enum" in schema and data not in schema["enum"]:
        raise InvalidResponseError(
            f"structured output failed schema: {data!r} not in enum {schema['enum']}"
        )
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                raise InvalidResponseError(
                    f"structured output failed schema: required field {key!r} missing: "
                    f"{_truncate(str(data))}"
                )
        for key, sub in schema.get("properties", {}).items():
            if key in data:
                _validate_against_schema(data[key], sub)
    if isinstance(data, list) and "items" in schema:
        for item in data:
            _validate_against_schema(item, schema["items"])


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True  # unknown type keyword: do not reject


def build_llm_client(
    settings: LLMSettings,
    transport: Transport | None = None,
    clock: Clock | None = None,
    *,
    random: Any = _random_module,
) -> LLMClient:
    """Build the single LLM client for the resolved configuration.

    Constructed once by the entrypoint and injected into the bot/handlers.
    ``transport`` and ``clock`` are the test seams: every contract test
    injects a stub transport (and a frozen clock) — no network, no provider.
    """
    return LLMClient(settings=settings, transport=transport, clock=clock, random=random)
