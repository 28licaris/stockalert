"""Tests for GET /api/health/services — the composite cockpit probe.

The endpoint must be best-effort: a failing subsystem becomes an
`error` state on its row, NEVER a 5xx. The Status page should always
be reachable.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main_api import app


def test_health_services_shape_and_200():
    """The endpoint always returns 200 with the documented shape."""
    client = TestClient(app)
    r = client.get("/api/health/services")
    assert r.status_code == 200
    body = r.json()
    # Top-level shape
    assert set(body.keys()) >= {"server_time", "services", "backfill", "monitors"}
    assert isinstance(body["services"], list)
    assert len(body["services"]) >= 1
    # Every service entry has the documented fields
    for svc in body["services"]:
        assert {"name", "state", "detail"}.issubset(svc.keys())
        assert svc["state"] in {"ok", "warn", "error", "unknown"}
    # Backfill summary fields
    for f in ("queued", "in_flight", "completed_recent"):
        assert f in body["backfill"]
    # Monitor summary fields
    for f in ("started", "errors"):
        assert f in body["monitors"]


def test_health_services_returns_200_when_clickhouse_throws():
    """A subsystem probe raising must NOT 5xx — it surfaces as state=error.

    routes_health imports `ping` lazily inside _check_clickhouse, so the
    patch target is the original module.
    """
    with patch("app.db.ping", side_effect=RuntimeError("boom")):
        client = TestClient(app)
        r = client.get("/api/health/services")
    assert r.status_code == 200
    body = r.json()
    ch = next(s for s in body["services"] if s["name"] == "ClickHouse")
    assert ch["state"] == "error"
    assert "boom" in ch["detail"]


def test_health_services_lists_expected_subsystems():
    """The four headline subsystems are present even if any return 'unknown'."""
    client = TestClient(app)
    r = client.get("/api/health/services")
    names = {s["name"] for s in r.json()["services"]}
    assert {"ClickHouse", "Iceberg", "Schwab", "Polygon"}.issubset(names)
