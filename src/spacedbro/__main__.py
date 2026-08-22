"""SpacedBro entrypoint.

Boots the process in order: load settings (fail fast with exit code 78 /
EX_CONFIG on any configuration error, BEFORE the service port is bound),
apply Alembic migrations (before any traffic), build the bot (repositories
+ send seam), start the in-process APScheduler (BON-33 — the proactive
pass is wired to the bot's send seam and its session factory), start the
health server, then run Telegram long polling.

**Single instance:** the in-process scheduler has no distributed lock —
run exactly one bot replica (README "Proactive scheduling (operator
note)"; ``spacedbro.scheduler`` module docstring).
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
from spacedbro.metrics import Metrics
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

    # 2. Metrics (BON-34, tasks.md §7): one process-wide object — the LLM
    #    client records call counts, the health server serves /metrics
    #    (item/user gauges refreshed from the bot's session factory).
    metrics = Metrics()

    # 3. LLM client — constructed here and injected into the bot/handlers
    #    (handlers never instantiate provider clients themselves).
    llm_client = build_llm_client(llm_settings, clock=clock, metrics=metrics)

    # 4. Bot application — owns the repositories (the only persistent
    #    state, BON-29) and the proactive send seam (BON-33).
    app = build_bot(settings.bot_token, llm_client, settings.database_url, clock)

    # Metrics gauges read the SAME SQLite the bot writes to.
    metrics.bind_session_factory(app.session_factory)

    # 5. In-process scheduler (single instance, same process as the bot —
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

    # 6. Health server (target of the container HEALTHCHECK; serves
    #    /healthz and the BON-34 /metrics endpoint).
    await start_health_server(
        settings.health_host,
        settings.health_port,
        settings.database_url,
        clock,
        metrics=metrics,
    )

    # 7. Telegram long polling — blocks until the process is stopped.
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
