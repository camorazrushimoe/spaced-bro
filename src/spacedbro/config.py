"""Application configuration loaded exclusively from environment variables.

No secrets live in code or committed files; the owner supplies ``BOT_TOKEN``
out of band. ``OPENAI_API_KEY`` is deliberately NOT required here: its
requirement is provider-driven and enforced by the LLM configuration layer
(``spacedbro.llm.config``, llm-router spec — "Provider-driven key check").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_DATABASE_URL = "sqlite:////data/spacedbro.db"
DEFAULT_HEALTH_HOST = "0.0.0.0"
DEFAULT_HEALTH_PORT = 8080
DEFAULT_LOG_LEVEL = "INFO"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    openai_api_key: str = ""
    database_url: str = DEFAULT_DATABASE_URL
    health_host: str = DEFAULT_HEALTH_HOST
    health_port: int = DEFAULT_HEALTH_PORT
    log_level: str = DEFAULT_LOG_LEVEL


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build settings from environment variables.

    ``BOT_TOKEN`` is required. ``OPENAI_API_KEY`` is optional at this layer
    (see module docstring). ``DATABASE_URL`` defaults to the Docker volume
    path (``sqlite:////data/spacedbro.db``).
    """
    env = dict(os.environ if environ is None else environ)

    bot_token = env.get("BOT_TOKEN", "").strip()
    openai_api_key = env.get("OPENAI_API_KEY", "").strip()

    if not bot_token:
        raise ConfigError("Missing required environment variable: BOT_TOKEN")

    database_url = env.get("DATABASE_URL", DEFAULT_DATABASE_URL).strip() or DEFAULT_DATABASE_URL
    health_host = env.get("HEALTH_HOST", DEFAULT_HEALTH_HOST).strip() or DEFAULT_HEALTH_HOST
    health_port = int(env.get("HEALTH_PORT", DEFAULT_HEALTH_PORT))
    log_level = env.get("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper() or DEFAULT_LOG_LEVEL

    return Settings(
        bot_token=bot_token,
        openai_api_key=openai_api_key,
        database_url=database_url,
        health_host=health_host,
        health_port=health_port,
        log_level=log_level,
    )
