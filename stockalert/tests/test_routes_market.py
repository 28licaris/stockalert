"""Tests for GET /api/market/banner."""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient


class _FakeQuotesProvider:
    async def get_quotes(self, symbols: list[str]) -> dict:
        out: dict = {}
        invalid: list[str] = []
        for sym in symbols:
            if sym == "/BAD":
                invalid.append(sym)
                continue
            out[sym] = {
                "assetMainType": "FUTURE" if sym.startswith("/") else "INDEX",
                "quote": {
                    "lastPrice": 5000.0 if sym.startswith("/") else 4500.0,
                    "netPercentChange": -0.25,
                    "closePrice": 5010.0,
                },
                "reference": {"description": "Test " + sym},
            }
        if invalid:
            out["errors"] = {"invalidSymbols": invalid}
        return out


@pytest.fixture
def app_client():
    from app.main_api import app

    @asynccontextmanager
    async def _noop(_app):
        yield

    app.router.lifespan_context = _noop
    with TestClient(app) as c:
        yield c


def test_market_banner_returns_rows(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.routes_market.get_market_quotes_provider", lambda: _FakeQuotesProvider())
    r = app_client.get("/api/market/banner?symbols=%24SPX,%2FMNQM26")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    syms = {x["symbol"] for x in body["items"]}
    assert "$SPX" in syms and "/MNQM26" in syms


def test_market_banner_no_get_quotes(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.routes_market.get_market_quotes_provider", lambda: object())
    r = app_client.get("/api/market/banner?symbols=SPY")
    assert r.status_code == 200
    assert r.json()["errors"]


def test_market_banner_empty_provider_payload(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    class P:
        async def get_quotes(self, symbols: list[str]) -> dict:
            return {}

    monkeypatch.setattr("app.api.routes_market.get_market_quotes_provider", lambda: P())
    r = app_client.get("/api/market/banner?symbols=SPY")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert any("empty quotes" in (e.get("message") or "") for e in body["errors"])


@pytest.mark.asyncio
async def test_fetch_quotes_merged_batches() -> None:
    from app.api import routes_market as rm

    calls: list[int] = []

    async def getter(syms: list[str]) -> dict:
        calls.append(len(syms))
        return {s: {"quote": {"lastPrice": 1.0, "closePrice": 1.0}} for s in syms}

    want = [f"S{i:03d}" for i in range(55)]
    merged = await rm._fetch_quotes_merged(getter, want)
    assert sum(calls) == 55
    assert len(calls) >= 2
    assert len(merged) == 55
