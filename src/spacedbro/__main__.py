"""SpacedBro entrypoint.

Boots the process in order: apply Alembic migrations (before any traffic),
start the in-process APScheduler, start the health server, then run Telegram
long polling.
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
from spacedbro.migrations import run_migrations
from spacedbro.scheduler import build_scheduler

logger = logging.getLogger(__name__)


async def _run() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    configure_logging(settings.log_level)

    # 1. Migrate before any traffic.
    run_migrations(settings.database_url)

    clock = UtcClock()

    # 2. In-process scheduler (single instance, same process as the bot).
    scheduler = build_scheduler(clock)
    scheduler.start()
    logger.info("In-process APScheduler started")

    # 3. Health server (target of the container HEALTHCHECK).
    await start_health_server(
        settings.health_host,
        settings.health_port,
        settings.database_url,
        clock,
    )

    # 4. Telegram long polling — blocks until the process is stopped.
    bot = build_bot(settings.bot_token)
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
