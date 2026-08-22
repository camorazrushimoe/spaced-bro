"""Tiny HTTP health + metrics server for the container HEALTHCHECK.

``/healthz`` is the probe target (process up + SQLite openable);
``/metrics`` (BON-34, tasks.md §7) serves the process metrics — LLM call
counts and item/user gauges — in Prometheus text format. The metrics
endpoint exists only when a :class:`~spacedbro.metrics.Metrics` instance
is injected (tests that build the app without metrics assert 404).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiohttp import web
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from spacedbro.clock import Clock

if TYPE_CHECKING:
    from spacedbro.metrics import Metrics

logger = logging.getLogger(__name__)


def _build_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def build_health_app(database_url: str, clock: Clock, metrics: "Metrics | None" = None) -> web.Application:
    """Build the aiohttp app exposing ``GET /healthz`` (and ``/metrics``)."""
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

    if metrics is not None:
        metrics.bind_clock(clock)

        async def metrics_handler(request: web.Request) -> web.Response:
            # Gauges are refreshed per scrape (best-effort, never raises);
            # counters are in-process and always current.
            metrics.refresh_gauges()
            return web.Response(
                text=metrics.render(), content_type="text/plain; version=0.0.4"
            )

        app.router.add_get("/metrics", metrics_handler)

    return app


async def start_health_server(
    host: str,
    port: int,
    database_url: str,
    clock: Clock,
    metrics: "Metrics | None" = None,
) -> web.AppRunner:
    """Start the health server on ``host:port`` and return its runner."""
    runner = web.AppRunner(build_health_app(database_url, clock, metrics=metrics))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Health server listening on http://%s:%d", host, port)
    return runner
