"""Contract tests for the /api/v1/calendar endpoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_calendar


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes_calendar.router, prefix="/api/v1")
    return TestClient(app)


def test_calendar_range_marks_holiday_and_open_days():
    c = _client()
    r = c.get("/api/v1/calendar", params={
        "start": "2026-06-17", "end": "2026-06-21", "asset_class": "equities",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["asset_class"] == "equities"
    by_date = {d["date"]: d for d in body["days"]}
    assert by_date["2026-06-18"]["status"] == "open"
    assert by_date["2026-06-19"]["status"] == "closed"           # Juneteenth
    assert "Juneteenth" in by_date["2026-06-19"]["reason"]
    assert by_date["2026-06-20"]["status"] == "closed"           # Saturday
    # events present + empty (Phase 2 contract)
    assert by_date["2026-06-18"]["events"] == []


def test_calendar_early_close_reported():
    c = _client()
    r = c.get("/api/v1/calendar", params={
        "start": "2026-11-27", "end": "2026-11-27", "asset_class": "equities",
    })
    day = r.json()["days"][0]
    assert day["status"] == "early_close"
    assert day["early_close_et"] == "13:00"


def test_futures_open_on_juneteenth():
    c = _client()
    r = c.get("/api/v1/calendar", params={
        "start": "2026-06-19", "end": "2026-06-19", "asset_class": "futures",
    })
    assert r.json()["days"][0]["status"] in ("open", "early_close")


def test_end_before_start_400():
    c = _client()
    r = c.get("/api/v1/calendar", params={"start": "2026-06-21", "end": "2026-06-17"})
    assert r.status_code == 400


def test_range_too_large_400():
    c = _client()
    r = c.get("/api/v1/calendar", params={"start": "2020-01-01", "end": "2026-01-01"})
    assert r.status_code == 400


def test_invalid_asset_class_422():
    c = _client()
    r = c.get("/api/v1/calendar", params={
        "start": "2026-06-17", "end": "2026-06-18", "asset_class": "crypto",
    })
    assert r.status_code == 422  # FastAPI Literal validation
