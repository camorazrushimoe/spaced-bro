"""Tests for environment-only configuration loading."""

from __future__ import annotations

import pytest

from spacedbro.config import (
    DEFAULT_DATABASE_URL,
    ConfigError,
    load_settings,
)


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_TOKEN": "123456:ABC-DEF",
        "OPENAI_API_KEY": "sk-test",
    }
    env.update(overrides)
    return env


def test_loads_required_values_from_env() -> None:
    settings = load_settings(_env())

    assert settings.bot_token == "123456:ABC-DEF"
    assert settings.openai_api_key == "sk-test"


def test_database_url_defaults_to_volume_path() -> None:
    settings = load_settings(_env())

    assert settings.database_url == DEFAULT_DATABASE_URL


def test_database_url_from_env() -> None:
    settings = load_settings(_env(DATABASE_URL="sqlite:////tmp/other.db"))

    assert settings.database_url == "sqlite:////tmp/other.db"


def test_missing_bot_token_raises() -> None:
    env = _env()
    env.pop("BOT_TOKEN")

    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_settings(env)


def test_missing_openai_key_is_not_required_here() -> None:
    # The OPENAI_API_KEY requirement is provider-driven and lives in the LLM
    # configuration layer (llm-router spec — "Provider-driven key check");
    # this layer must not enforce it unconditionally.
    env = _env()
    env.pop("OPENAI_API_KEY")

    settings = load_settings(env)
    assert settings.openai_api_key == ""


def test_blank_values_are_treated_as_missing() -> None:
    with pytest.raises(ConfigError):
        load_settings(_env(BOT_TOKEN="   "))
