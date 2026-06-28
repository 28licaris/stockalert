"""Contract tests for the /api/v1/calendar endpoint."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_calendar
from app.services import market_events as me


@pytest.fixture(autouse=True)
def _no_ch_events(monkeypatch):
    """Isolate from ClickHouse: computed + seeded events still flow; the CH
    (corp-actions) source returns []. Keeps these API tests fast + hermetic."""
    monkeypatch.setattr(me, "ch_events", lambda *a, **k: [])


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
    # 6/18: 3rd-Friday OPEX shifted off Juneteenth → quad-witching (June).
    types_618 = {e["event_type"] for e in by_date["2026-06-18"]["events"]}
    assert "quad_witching" in types_618


def test_calendar_includes_fomc_event():
    c = _client()
    r = c.get("/api/v1/calendar", params={
        "start": "2026-06-01", "end": "2026-06-30", "asset_class": "equities",
    })
    by_date = {d["date"]: d for d in r.json()["days"]}
    fomc = [e for e in by_date["2026-06-17"]["events"] if e["event_type"] == "fomc"]
    assert fomc and fomc[0]["importance"] == "high" and fomc[0]["time_et"] == "14:00"


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
