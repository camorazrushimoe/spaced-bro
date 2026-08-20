"""Tests for the HTTP health endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

from aiohttp.test_utils import TestClient, TestServer

from spacedbro.clock import FrozenClock
from spacedbro.health import build_health_app


def _clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc))


async def test_healthz_ok_when_database_reachable(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'spacedbro.db'}"
    app = build_health_app(db_url, _clock())

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["time"] == "2026-08-20T12:00:00+00:00"


async def test_healthz_degraded_when_database_unreachable(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'missing_dir' / 'spacedbro.db'}"
    app = build_health_app(db_url, _clock())

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "degraded"
        assert body["database"] == "unavailable"
