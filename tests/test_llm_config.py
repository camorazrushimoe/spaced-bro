"""Tests for LLM environment configuration (llm-router spec, Configuration layer).

Spec: openspec/changes/llm-router/specs/llm-client/spec.md
- Environment-driven configuration / APP_ENV validation
- Pinned default backends
- Partial-override composition rule
- LLM_PROVIDER validation
- Startup fail-fast contract (resolver level; process exit 78 tested in
  tests/test_llm_main_config.py)
- Provider-driven key check
- Startup logging
"""

from __future__ import annotations

import logging

import pytest

from spacedbro.config import ConfigError
from spacedbro.llm.config import load_llm_settings


def _env(**overrides: str) -> dict[str, str]:
    env: dict[str, str] = {}
    env.update(overrides)
    return env


# --- Pinned default backends -------------------------------------------------


def test_development_pinned_defaults() -> None:
    settings = load_llm_settings(_env(APP_ENV="development"))

    assert settings.app_env == "development"
    assert settings.provider == "openai_compatible"
    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.model == "gemma-4-e2b-it"
    assert settings.api_key == "local-dev"
    assert settings.timeout_seconds == 30
    assert settings.max_retries == 2


def test_development_api_key_never_read_from_openai_api_key() -> None:
    settings = load_llm_settings(_env(APP_ENV="development", OPENAI_API_KEY="sk-real"))

    assert settings.api_key == "local-dev"


def test_production_pinned_defaults() -> None:
    settings = load_llm_settings(_env(APP_ENV="production", OPENAI_API_KEY="sk-prod"))

    assert settings.provider == "openai"
    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.model == "gpt-5.6-luna"
    assert settings.api_key == "sk-prod"


def test_preprod_pinned_defaults_identical_to_production() -> None:
    settings = load_llm_settings(_env(APP_ENV="preprod", OPENAI_API_KEY="sk-pp"))

    assert settings.provider == "openai"
    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.model == "gpt-5.6-luna"
    assert settings.api_key == "sk-pp"


# --- APP_ENV validation -------------------------------------------------------


def test_missing_app_env_fails_fast_naming_variable_and_allowed_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR), pytest.raises(ConfigError, match="APP_ENV"):
        load_llm_settings(_env())

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("APP_ENV" in r.message for r in errors)
    assert any(
        all(v in r.message for v in ("development", "preprod", "production"))
        for r in errors
    )


def test_empty_app_env_fails_fast() -> None:
    with pytest.raises(ConfigError, match="APP_ENV"):
        load_llm_settings(_env(APP_ENV=""))


def test_unknown_app_env_fails_fast_instead_of_production_like_backend() -> None:
    # "prod" typo must be a loud error, never a silent production-like backend.
    with pytest.raises(ConfigError, match="APP_ENV"):
        load_llm_settings(_env(APP_ENV="prod", OPENAI_API_KEY="sk-x"))


# --- Partial-override composition rule ----------------------------------------


def test_partial_override_composes_per_variable() -> None:
    settings = load_llm_settings(
        _env(APP_ENV="production", OPENAI_API_KEY="sk-prod", LLM_MODEL="other-model")
    )

    assert settings.provider == "openai"
    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.model == "other-model"
    assert settings.api_key == "sk-prod"


def test_empty_string_override_keeps_default_component() -> None:
    settings = load_llm_settings(_env(APP_ENV="development", LLM_MODEL=""))

    assert settings.model == "gemma-4-e2b-it"


def test_base_url_override_replaces_component() -> None:
    settings = load_llm_settings(
        _env(APP_ENV="development", LLM_BASE_URL="http://localhost:8080/v1")
    )

    assert settings.base_url == "http://localhost:8080/v1"
    assert settings.provider == "openai_compatible"
    assert settings.model == "gemma-4-e2b-it"


def test_provider_override_to_compatible_in_production() -> None:
    settings = load_llm_settings(
        _env(
            APP_ENV="production",
            LLM_PROVIDER="openai_compatible",
            LLM_BASE_URL="http://localhost:11434/v1",
            LLM_MODEL="gemma-4-e2b-it",
        )
    )

    assert settings.provider == "openai_compatible"
    assert settings.api_key == "local-dev"


# --- LLM_PROVIDER validation ---------------------------------------------------


def test_unknown_llm_provider_fails_fast(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR), pytest.raises(
        ConfigError, match="LLM_PROVIDER"
    ):
        load_llm_settings(_env(APP_ENV="development", LLM_PROVIDER="anthropic"))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("LLM_PROVIDER" in r.message for r in errors)
    assert any("openai" in r.message and "openai_compatible" in r.message for r in errors)


# --- Provider-driven key check --------------------------------------------------


def test_missing_key_for_resolved_openai_provider_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR), pytest.raises(
        ConfigError, match="OPENAI_API_KEY"
    ):
        load_llm_settings(_env(APP_ENV="production"))

    assert any("OPENAI_API_KEY" in r.message for r in caplog.records)


@pytest.mark.parametrize("key", ["", "   "])
def test_empty_or_whitespace_key_treated_as_missing(key: str) -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_llm_settings(_env(APP_ENV="preprod", OPENAI_API_KEY=key))


def test_local_override_in_production_does_not_require_cloud_key() -> None:
    settings = load_llm_settings(
        _env(
            APP_ENV="production",
            LLM_PROVIDER="openai_compatible",
            LLM_BASE_URL="http://localhost:11434/v1",
            LLM_MODEL="gemma-4-e2b-it",
        )
    )

    assert settings.api_key == "local-dev"


# --- Timeout / retries validation ------------------------------------------------


def test_timeout_and_retries_defaults() -> None:
    settings = load_llm_settings(_env(APP_ENV="development"))

    assert settings.timeout_seconds == 30
    assert settings.max_retries == 2


def test_timeout_and_retries_parsed_from_env() -> None:
    settings = load_llm_settings(
        _env(APP_ENV="development", LLM_TIMEOUT_SECONDS="60", LLM_MAX_RETRIES="0")
    )

    assert settings.timeout_seconds == 60
    assert settings.max_retries == 0


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_invalid_timeout_fails_fast_naming_variable(value: str) -> None:
    with pytest.raises(ConfigError, match="LLM_TIMEOUT_SECONDS"):
        load_llm_settings(_env(APP_ENV="development", LLM_TIMEOUT_SECONDS=value))


@pytest.mark.parametrize("value", ["-1", "abc", "1.5"])
def test_invalid_max_retries_fails_fast_naming_variable(value: str) -> None:
    with pytest.raises(ConfigError, match="LLM_MAX_RETRIES"):
        load_llm_settings(_env(APP_ENV="development", LLM_MAX_RETRIES=value))


# --- Startup logging ---------------------------------------------------------------


def test_startup_info_log_contains_resolved_configuration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        load_llm_settings(_env(APP_ENV="development"))

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        all(
            part in r.message
            for part in ("development", "openai_compatible", "gemma-4-e2b-it", "localhost:11434/v1")
        )
        for r in infos
    )


# --- Determinism ---------------------------------------------------------------------


def test_resolution_is_a_pure_function_of_the_environment() -> None:
    env = _env(APP_ENV="production", OPENAI_API_KEY="sk-x", LLM_MODEL="m1")

    assert load_llm_settings(dict(env)) == load_llm_settings(dict(env))
