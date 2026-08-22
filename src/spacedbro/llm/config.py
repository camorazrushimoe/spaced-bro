"""LLM configuration — environment-driven, deterministic, fail-fast.

Source of truth: ``openspec/changes/llm-router/``
- specs/llm-client/spec.md: "Environment-driven configuration",
  "APP_ENV validation", "Pinned default backends",
  "Partial-override composition rule", "LLM_PROVIDER validation",
  "Startup fail-fast contract", "Provider-driven key check",
  "Startup logging".
- design.md: "Environment model", "Configuration (environment variables)",
  "Default resolution logic (deterministic)", "Fail-fast contract".

Resolution is a pure function of the environment mapping handed in: same
environment, same resolved tuple — no runtime discovery, no heuristic
fallback, no implicit default outside the rules in the spec.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

from spacedbro.config import ConfigError

logger = logging.getLogger(__name__)

#: Exactly these values — anything else (including unset/empty) is a
#: configuration error; there is no "else: production-like" catch-all.
ALLOWED_APP_ENVS: tuple[str, ...] = ("development", "preprod", "production")

#: Exactly these provider values.
ALLOWED_PROVIDERS: tuple[str, ...] = ("openai", "openai_compatible")

#: Fixed development api_key. Never read from ``OPENAI_API_KEY`` so a real
#: key can never reach the local (insecure) transport.
DEV_API_KEY_SENTINEL = "local-dev"

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 2

_OPENAI_BASE_URL = "https://api.openai.com/v1"

#: Pinned default row per resolved APP_ENV: (provider, base_url, model).
#: api_key is provider-driven (see resolution below), not a per-env value.
PINNED_DEFAULTS: Mapping[str, tuple[str, str, str]] = {
    "development": ("openai_compatible", "http://localhost:11434/v1", "gemma-4-e2b-it"),
    "preprod": ("openai", _OPENAI_BASE_URL, "gpt-5.6-luna"),
    "production": ("openai", _OPENAI_BASE_URL, "gpt-5.6-luna"),
}


@dataclass(frozen=True, slots=True)
class LLMSettings:
    """The resolved LLM configuration — one deterministic tuple per environment."""

    app_env: str
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: int
    max_retries: int


def _fail(variable: str, allowed: str) -> ConfigError:
    """Build (and log at ERROR) a fail-fast configuration error.

    The process-level contract (exit code 78 / EX_CONFIG before the service
    port is bound) is enforced by the entrypoint, which catches
    ``ConfigError``; the resolver logs the offending variable and the
    allowed values here so the ERROR line always exists.
    """
    message = f"{variable} must be one of: {allowed}"
    logger.error("%s", message)
    return ConfigError(message)


def load_llm_settings(environ: Mapping[str, str] | None = None) -> LLMSettings:
    """Resolve the LLM configuration from environment variables.

    Raises ``ConfigError`` (naming the offending variable and the allowed
    values) on any configuration error; the entrypoint maps that to exit
    code 78 (EX_CONFIG).
    """
    env = dict(os.environ if environ is None else environ)

    # 1. Validate APP_ENV — exactly {development, preprod, production}.
    app_env = env.get("APP_ENV", "").strip()
    if app_env not in ALLOWED_APP_ENVS:
        raise _fail("APP_ENV", ", ".join(ALLOWED_APP_ENVS))

    # 2. Pinned default row for the resolved APP_ENV.
    default_provider, default_base_url, default_model = PINNED_DEFAULTS[app_env]

    # 3. Per-variable composition: each set, non-empty variable replaces
    #    exactly its own component; unset components keep the default.
    #    Mixed states are legal; no cross-field consistency is validated.
    provider = env.get("LLM_PROVIDER", "").strip() or default_provider
    base_url = env.get("LLM_BASE_URL", "").strip() or default_base_url
    model = env.get("LLM_MODEL", "").strip() or default_model
    if provider not in ALLOWED_PROVIDERS:
        raise _fail("LLM_PROVIDER", ", ".join(ALLOWED_PROVIDERS))

    # 4. The key check is provider-driven, not environment-driven:
    #    OPENAI_API_KEY is consulted only when the resolved provider is
    #    `openai`; otherwise the fixed local sentinel is used.
    if provider == "openai":
        api_key = env.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise _fail("OPENAI_API_KEY", "a non-empty OpenAI API key")
    else:
        api_key = DEV_API_KEY_SENTINEL

    # 5. Validate timeout / retries.
    timeout_seconds = _parse_positive_int(
        env.get("LLM_TIMEOUT_SECONDS", "").strip() or str(DEFAULT_TIMEOUT_SECONDS),
        "LLM_TIMEOUT_SECONDS",
    )
    max_retries = _parse_non_negative_int(
        env.get("LLM_MAX_RETRIES", "").strip() or str(DEFAULT_MAX_RETRIES),
        "LLM_MAX_RETRIES",
    )

    # 6. INFO log: the verify-after-change signal for deploys.
    logger.info(
        "LLM resolved configuration: APP_ENV=%s provider=%s model=%s base_url=%s",
        app_env,
        provider,
        model,
        base_url,
    )

    return LLMSettings(
        app_env=app_env,
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def _parse_positive_int(raw: str, variable: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise _fail(variable, "a positive integer (e.g. 30)") from None
    if value <= 0:
        raise _fail(variable, "a positive integer (e.g. 30)")
    return value


def _parse_non_negative_int(raw: str, variable: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise _fail(variable, "a non-negative integer (e.g. 0, 1, 2)") from None
    if value < 0:
        raise _fail(variable, "a non-negative integer (e.g. 0, 1, 2)")
    return value
