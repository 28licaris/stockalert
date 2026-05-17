"""Tests for GET /api/market/banner."""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.api.routes_market import get_quote_service
from app.services.readers.quote_service import QuoteService


class _FakeQuotesProvider:
    """
    Stub provider that mimics Schwab's quote-payload shape for banner
    tests — nested `quote` / `reference` / `assetMainType` blocks plus
    an optional `errors.invalidSymbols` block when callers ask for
    obviously-bogus symbols.
    """

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


def _override(app_client, provider) -> None:
    """Helper: swap the FastAPI dependency to use a stub QuoteService."""
    from app.main_api import app

    app.dependency_overrides[get_quote_service] = lambda: QuoteService(provider)


def _clear_override() -> None:
    from app.main_api import app

    app.dependency_overrides.pop(get_quote_service, None)


def test_market_banner_returns_rows(app_client) -> None:
    _override(app_client, _FakeQuotesProvider())
    try:
        r = app_client.get("/api/market/banner?symbols=%24SPX,%2FMNQM26")
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 2
        syms = {x["symbol"] for x in body["items"]}
        assert "$SPX" in syms and "/MNQM26" in syms
    finally:
        _clear_override()


def test_market_banner_no_get_quotes(app_client) -> None:
    """Provider lacking `get_quotes` -> 200 with an error message."""
    _override(app_client, object())  # bare object has no get_quotes
    try:
        r = app_client.get("/api/market/banner?symbols=SPY")
        assert r.status_code == 200
        assert r.json()["errors"]
    finally:
        _clear_override()


def test_market_banner_empty_provider_payload(app_client) -> None:
    """Provider returns {} -> 200 with the 'empty quotes' message."""
    class P:
        async def get_quotes(self, symbols: list[str]) -> dict:
            return {}

    _override(app_client, P())
    try:
        r = app_client.get("/api/market/banner?symbols=SPY")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert any("empty quotes" in (e.get("message") or "") for e in body["errors"])
    finally:
        _clear_override()


def test_market_banner_invalid_symbols_surface_in_errors(app_client) -> None:
    """`errors.invalidSymbols` from the provider passes through to the response."""
    _override(app_client, _FakeQuotesProvider())
    try:
        r = app_client.get("/api/market/banner?symbols=%24SPX,%2FBAD,%2FMNQM26")
        assert r.status_code == 200
        body = r.json()
        # /BAD is rejected; the other two succeed.
        syms = {x["symbol"] for x in body["items"]}
        assert syms == {"$SPX", "/MNQM26"}
        invalid_in_errors = {e.get("symbol") for e in body["errors"] if e.get("symbol")}
        assert "/BAD" in invalid_in_errors
    finally:
        _clear_override()


@pytest.mark.asyncio
async def test_quote_service_chunking_behavior() -> None:
    """Chunking behavior moved from routes_market into QuoteService."""
    calls: list[int] = []

    class _BatchTrackingProvider:
        async def get_quotes(self, syms: list[str]) -> dict:
            calls.append(len(syms))
            return {s: {"quote": {"lastPrice": 1.0, "closePrice": 1.0}} for s in syms}

    svc = QuoteService(_BatchTrackingProvider())
    want = [f"S{i:03d}" for i in range(55)]
    merged, invalid = await svc.get_raw_quotes(want)
    assert sum(calls) == 55
    assert len(calls) >= 2  # at least two chunks for 55 symbols
    assert len(merged) == 55
    assert invalid == []
