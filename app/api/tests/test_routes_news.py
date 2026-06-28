"""Route tests for /api/v1/news — minimal app, read_news monkeypatched."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_news
from app.services.news.schemas import NewsItem


def _client():
    app = FastAPI()
    app.include_router(routes_news.router, prefix="/api/v1")
    return TestClient(app)


def test_parses_csv_filters(monkeypatch):
    captured = {}

    def fake_read_news(*, symbols, event_types, since, limit):
        captured.update(symbols=symbols, event_types=event_types, since=since, limit=limit)
        return []

    monkeypatch.setattr(routes_news, "read_news", fake_read_news)
    r = _client().get("/api/v1/news?symbols=AAPL,NVDA&types=8-K,4&limit=5")
    assert r.status_code == 200
    assert captured["symbols"] == ["AAPL", "NVDA"]
    assert captured["event_types"] == ["8-K", "4"]
    assert captured["limit"] == 5


def test_returns_serialized_items(monkeypatch):
    item = NewsItem(
        id="acc-1", published_at=datetime(2026, 6, 27, 16, 0, tzinfo=timezone.utc),
        source="edgar", event_type="8-K", symbol="AAPL", title="8-K - Apple",
        url="https://sec.gov/x", summary="S", why_it_matters="W",
        materiality="high", sentiment="positive", enriched=True,
    )
    monkeypatch.setattr(routes_news, "read_news", lambda **_: [item])
    r = _client().get("/api/v1/news")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["enriched"] is True
    assert body[0]["materiality"] == "high"


def test_empty_filters_pass_none(monkeypatch):
    captured = {}
    monkeypatch.setattr(routes_news, "read_news",
                        lambda **kw: captured.update(kw) or [])
    r = _client().get("/api/v1/news")
    assert r.status_code == 200
    assert captured["symbols"] is None and captured["event_types"] is None


def test_reader_error_is_500(monkeypatch):
    def boom(**_):
        raise RuntimeError("ch down")

    monkeypatch.setattr(routes_news, "read_news", boom)
    r = _client().get("/api/v1/news")
    assert r.status_code == 500
    assert "news error" in r.json()["detail"]


def test_digest_window_and_materiality(monkeypatch):
    captured = {}

    def fake_read_news(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(routes_news, "read_news", fake_read_news)
    r = _client().get("/api/v1/news/digest?date=2026-06-17")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-06-17"
    assert body["count"] == 0
    # Defaults: high-only, enriched-only, one ET-day window.
    assert captured["materiality"] == ["high"]
    assert captured["enriched_only"] is True
    assert captured["since"] is not None and captured["until"] is not None
    assert captured["until"] > captured["since"]


def test_digest_returns_items(monkeypatch):
    item = NewsItem(
        id="acc-1", published_at=datetime(2026, 6, 17, 16, 0, tzinfo=timezone.utc),
        source="edgar", event_type="8-K", symbol="AAPL", title="t",
        url="https://sec.gov/x", summary="S", why_it_matters="W",
        materiality="high", sentiment="positive", enriched=True,
    )
    monkeypatch.setattr(routes_news, "read_news", lambda **_: [item])
    r = _client().get("/api/v1/news/digest?date=2026-06-17")
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["symbol"] == "AAPL"
