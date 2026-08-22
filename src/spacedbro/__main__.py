"""SpacedBro entrypoint.

Boots the process in order: load settings (fail fast with exit code 78 /
EX_CONFIG on any configuration error, BEFORE the service port is bound),
apply Alembic migrations (before any traffic), start the in-process
APScheduler, build the LLM client (the only door to the LLM, injected into
handlers), start the health server, then run Telegram long polling.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramUnauthorizedError

from spacedbro.bot.app import build_bot
from spacedbro.clock import UtcClock
from spacedbro.config import ConfigError, load_settings
from spacedbro.health import start_health_server
from spacedbro.logging_config import configure_logging
from spacedbro.llm.client import build_llm_client
from spacedbro.llm.config import load_llm_settings
from spacedbro.migrations import run_migrations
from spacedbro.scheduler import build_scheduler

logger = logging.getLogger(__name__)

#: sysexits.h EX_CONFIG — the startup fail-fast contract of the llm-router
#: spec: exit code 78 on every configuration error, before the port binds.
EXIT_CONFIG = 78


async def _run() -> None:
    # Configuration errors abort the process with exit code 78 (EX_CONFIG)
    # without binding the service port. The resolvers log an ERROR line
    # naming the offending variable; this re-logs for the process log.
    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(EXIT_CONFIG) from exc

    configure_logging(settings.log_level)

    try:
        llm_settings = load_llm_settings()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(EXIT_CONFIG) from exc

    # 1. Migrate before any traffic.
    run_migrations(settings.database_url)

    clock = UtcClock()

    # 2. In-process scheduler (single instance, same process as the bot).
    scheduler = build_scheduler(clock)
    scheduler.start()
    logger.info("In-process APScheduler started")

    # 3. LLM client — constructed here and injected into the bot/handlers
    #    (handlers never instantiate provider clients themselves).
    llm_client = build_llm_client(llm_settings, clock=clock)

    # 4. Health server (target of the container HEALTHCHECK).
    await start_health_server(
        settings.health_host,
        settings.health_port,
        settings.database_url,
        clock,
    )

    # 5. Telegram long polling — blocks until the process is stopped.
    bot = build_bot(settings.bot_token, llm_client, settings.database_url, clock)
    try:
        await bot.run()
    except TelegramUnauthorizedError as exc:
        logger.error("Telegram rejected BOT_TOKEN (401 Unauthorized): %s", exc)
        raise
    finally:
        scheduler.shutdown(wait=False)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
