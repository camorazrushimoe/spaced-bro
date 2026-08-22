"""SpacedBro entrypoint.

Boots the process in order: load settings (fail fast with exit code 78 /
EX_CONFIG on any configuration error, BEFORE the service port is bound),
apply Alembic migrations (before any traffic), build the bot (repositories
+ send seam), start the in-process APScheduler (BON-33 — the proactive
pass is wired to the bot's send seam and its session factory), start the
health server, then run Telegram long polling.

**Single instance (operator note, scheduler spec "Single-instance"):** the
scheduler runs IN-PROCESS with NO distributed lock — run exactly one bot
replica (see README "Single-instance note" and ``spacedbro.scheduler``'s
module docstring).
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

    # 2. LLM client — constructed here and injected into the bot/handlers
    #    (handlers never instantiate provider clients themselves).
    llm_client = build_llm_client(llm_settings, clock=clock)

    # 3. Bot application — owns the repositories (the only persistent
    #    state, BON-29) and the proactive send seam (BON-33).
    app = build_bot(settings.bot_token, llm_client, settings.database_url, clock)

    # 4. In-process scheduler (single instance, same process as the bot —
    #    NO distributed lock in MVP; the operator MUST run one replica).
    #    The tick reads/writes the same SQLite the bot uses, through the
    #    app's session factory, and sends via the bot's send seam.
    scheduler = build_scheduler(
        clock,
        settings.scheduler_interval_minutes,
        sender=app.send_message,
        session_factory=app.session_factory,
        dry_run=settings.scheduler_dry_run,
    )
    scheduler.start()
    logger.info(
        "In-process APScheduler started (interval=%dm dry_run=%s)",
        settings.scheduler_interval_minutes,
        settings.scheduler_dry_run,
    )

    # 5. Health server (target of the container HEALTHCHECK).
    await start_health_server(
        settings.health_host,
        settings.health_port,
        settings.database_url,
        clock,
    )

    # 6. Telegram long polling — blocks until the process is stopped.
    try:
        await app.run()
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
