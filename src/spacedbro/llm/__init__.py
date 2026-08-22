"""SpacedBro LLM layer: environment-driven configuration and the client
abstraction that is the only door to any LLM provider.

Spec: ``openspec/changes/llm-router/`` (specs/llm-client/spec.md, design.md).

Application code depends only on the ``LLMClient`` protocol and the domain
errors here — provider-specific types never leak past this package.
"""

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

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "LLMSettings",
    "InvalidResponseError",
    "Message",
    "ProviderUnavailableError",
    "RateLimitError",
    "ResponseFormat",
    "TimeoutError",
    "Transport",
    "VisionNotSupportedError",
    "build_llm_client",
    "load_llm_settings",
]
