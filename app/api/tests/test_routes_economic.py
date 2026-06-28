"""Route tests for /api/v1/economic — minimal app, EconService mocked."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_economic
from app.services.news.econ import EconHistoryPoint, EconIndicator


def _client():
    app = FastAPI()
    app.include_router(routes_economic.router, prefix="/api/v1")
    return TestClient(app)


class _FakeEcon:
    def latest(self):
        return [EconIndicator(
            series_id="LNS14000000", name="Unemployment rate", unit="%",
            period_label="May 2026", value=4.1, value_label="4.1%",
            change=0.1, raw_value=4.1,
        )]

    def history(self, series_id, limit=60):
        return [EconHistoryPoint(period="2026-05", period_label="May 2026", value=4.1)]


def test_get_economic_returns_indicators(monkeypatch):
    monkeypatch.setattr(routes_economic.EconService, "from_settings", lambda: _FakeEcon())
    r = _client().get("/api/v1/economic")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["value_label"] == "4.1%"
    assert body[0]["name"] == "Unemployment rate"


def test_history_known_series(monkeypatch):
    monkeypatch.setattr(routes_economic.EconService, "from_settings", lambda: _FakeEcon())
    r = _client().get("/api/v1/economic/LNS14000000/history")
    assert r.status_code == 200
    assert r.json()[0]["period"] == "2026-05"


def test_history_unknown_series_is_404(monkeypatch):
    monkeypatch.setattr(routes_economic.EconService, "from_settings", lambda: _FakeEcon())
    r = _client().get("/api/v1/economic/NOPE/history")
    assert r.status_code == 404
