"""Domain error set of the LLM layer — exactly five classes.

Spec: ``openspec/changes/llm-router/specs/llm-client/spec.md`` —
"Domain error set" and "Failure-to-domain-error mapping"; design.md —
"Failure-to-domain-error mapping".

Provider-specific types (aiohttp / transport exceptions) MUST never escape
the client: every failure surfaces as exactly one of the five classes
below, each carrying a human-readable ``detail`` (status code, truncated
raw body for parse failures) so the mapping is diagnosable without
provider types leaking into the rest of the application.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class of the five LLM domain errors.

    ``detail`` is a human-readable explanation of the failure (HTTP status,
    truncated raw body, transport error kind). It is always set, and it is
    also what ``str(error)`` returns.
    """

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)


class TimeoutError(LLMError):  # noqa: A001 — name pinned by the spec
    """The client-side deadline (``LLM_TIMEOUT_SECONDS``) expired before a response."""


class RateLimitError(LLMError):
    """The provider rejected the request as rate-limited (HTTP 429)."""


class InvalidResponseError(LLMError):
    """The provider accepted the request but the body cannot be used:
    auth/other 4xx (401/403/400/404), structurally invalid chat-completions
    body, or malformed / schema-mismatched structured JSON."""


class ProviderUnavailableError(LLMError):
    """The provider could not be reached (transport-level: connection
    refused / DNS / network unreachable) or is overloaded (HTTP 5xx)."""

    def __init__(self, detail: str = "", *, retryable: bool = False) -> None:
        super().__init__(detail)
        #: True only when the cause is an HTTP 5xx response — the single
        #: retryable kind of this error. Connection-level failures are
        #: never retried (pinned retry policy, design.md).
        self.retryable = retryable


class VisionNotSupportedError(LLMError):
    """A vision request was issued but the resolved local model/endpoint
    does not support image input (400/404/422 from a local endpoint).

    First-class member of the domain set: the only way a caller can
    distinguish a permanent environment-level condition from a transient
    outage. Never retried.
    """
