"""Tiny HTTP health server for the container HEALTHCHECK."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from spacedbro.clock import Clock

logger = logging.getLogger(__name__)


def _build_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def build_health_app(database_url: str, clock: Clock) -> web.Application:
    """Build the aiohttp app exposing ``GET /healthz``."""
    engine = _build_engine(database_url)
    app = web.Application()

    def _database_ok() -> bool:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.exception("Health check: database not reachable")
            return False

    async def healthz(request: web.Request) -> web.Response:
        db_ok = await asyncio.to_thread(_database_ok)
        body = {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "unavailable",
            "time": clock.utc_now().isoformat(),
        }
        return web.json_response(body, status=200 if db_ok else 503)

    app.router.add_get("/healthz", healthz)
    return app


async def start_health_server(
    host: str,
    port: int,
    database_url: str,
    clock: Clock,
) -> web.AppRunner:
    """Start the health server on ``host:port`` and return its runner."""
    runner = web.AppRunner(build_health_app(database_url, clock))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Health server listening on http://%s:%d", host, port)
    return runner
