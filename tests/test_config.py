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


# --- Scheduler (BON-33, design §8) ---------------------------------------------


def test_scheduler_settings_defaults() -> None:
    settings = load_settings(_env())

    assert settings.scheduler_interval_minutes == 5
    assert settings.scheduler_dry_run is False


def test_scheduler_interval_minutes_from_env() -> None:
    settings = load_settings(_env(SCHEDULER_INTERVAL_MINUTES="30"))

    assert settings.scheduler_interval_minutes == 30


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "5.5"])
def test_scheduler_interval_minutes_invalid_raises(raw: str) -> None:
    with pytest.raises(ConfigError, match="SCHEDULER_INTERVAL_MINUTES"):
        load_settings(_env(SCHEDULER_INTERVAL_MINUTES=raw))


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_scheduler_dry_run_truthy(raw: str) -> None:
    settings = load_settings(_env(SCHEDULER_DRY_RUN=raw))

    assert settings.scheduler_dry_run is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
def test_scheduler_dry_run_falsy(raw: str) -> None:
    settings = load_settings(_env(SCHEDULER_DRY_RUN=raw))

    assert settings.scheduler_dry_run is False


def test_scheduler_dry_run_garbage_raises() -> None:
    # A typo'd flag must not silently flip the mode (live vs dry run).
    with pytest.raises(ConfigError, match="SCHEDULER_DRY_RUN"):
        load_settings(_env(SCHEDULER_DRY_RUN="tru"))
