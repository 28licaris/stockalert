"""
Unit tests for `SchwabProvider.search_instruments` + `GET /api/instruments/search`.

Both layers are exercised without touching the real Schwab API:
  - Provider tests monkey-patch `_market_data_get` to return canned responses.
  - Route tests inject a fake provider into `watchlist_service._provider`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.providers.schwab_provider import SchwabProvider


# ---------- SchwabProvider.search_instruments ----------


@pytest.mark.asyncio
async def test_search_uses_symbol_regex_for_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single letters/short prefixes should hit `symbol-regex`, not desc-search."""
    provider = SchwabProvider.__new__(SchwabProvider)  # bypass __init__ network calls
    calls: list[tuple[str, dict]] = []

    async def fake_get(path: str, params=None):
        calls.append((path, dict(params or {})))
        return {"instruments": [
            {"symbol": "NVDA", "description": "NVIDIA Corp",
             "exchange": "NASDAQ", "assetType": "EQUITY"},
            {"symbol": "NVDS", "description": "GraniteShares 2x Short NVDA Daily ETF",
             "exchange": "NASDAQ", "assetType": "EQUITY"},
        ]}

    monkeypatch.setattr(provider, "_market_data_get", fake_get)

    results = await provider.search_instruments("NVD", limit=10)

    # First call MUST be the symbol-regex projection with anchored prefix
    assert calls[0][0] == "/instruments"
    assert calls[0][1]["projection"] == "symbol-regex"
    assert calls[0][1]["symbol"] == "^NVD.*"

    assert len(results) == 2
    assert results[0]["symbol"] == "NVDA"
    assert results[0]["description"] == "NVIDIA Corp"
    assert results[0]["asset_type"] == "EQUITY"


@pytest.mark.asyncio
async def test_search_dedupes_across_projections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both prefix and desc projections may return overlap; result list dedupes."""
    provider = SchwabProvider.__new__(SchwabProvider)
    responses = iter([
        {"instruments": [
            {"symbol": "AAPL", "description": "APPLE INC", "exchange": "NASDAQ", "assetType": "EQUITY"},
        ]},
        {"instruments": [
            # AAPL again from desc-search — must NOT be duplicated
            {"symbol": "AAPL", "description": "APPLE INC", "exchange": "NASDAQ", "assetType": "EQUITY"},
            {"symbol": "AAPLW", "description": "APPLE WARRANT", "exchange": "OTC", "assetType": "EQUITY"},
        ]},
    ])

    async def fake_get(path: str, params=None):
        return next(responses)

    monkeypatch.setattr(provider, "_market_data_get", fake_get)
    results = await provider.search_instruments("AAPL", limit=10)

    syms = [r["symbol"] for r in results]
    assert syms == ["AAPL", "AAPLW"], "dedupe + ranking: exact AAPL match first"


@pytest.mark.asyncio
async def test_search_ranks_equity_above_mutual_fund(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typing 'apple' must surface AAPL (EQUITY) above APPLESEED (MUTUAL_FUND)."""
    provider = SchwabProvider.__new__(SchwabProvider)
    responses = iter([
        # symbol-regex: no symbols start with APPLE
        {"instruments": []},
        # desc-search: alphabetical-by-symbol from Schwab
        {"instruments": [
            {"symbol": "APPLX", "description": "APPLESEED INVESTOR",
             "exchange": "Mutual Fund", "assetType": "MUTUAL_FUND"},
            {"symbol": "APLE", "description": "APPLE HOSPITALITY RE REIT",
             "exchange": "NYSE", "assetType": "EQUITY"},
            {"symbol": "AAPL", "description": "APPLE INC",
             "exchange": "NASDAQ", "assetType": "EQUITY"},
            {"symbol": "APPIX", "description": "APPLESEED INSTITUTIONAL",
             "exchange": "Mutual Fund", "assetType": "MUTUAL_FUND"},
        ]},
    ])

    async def fake_get(path: str, params=None):
        return next(responses)

    monkeypatch.setattr(provider, "_market_data_get", fake_get)
    results = await provider.search_instruments("apple", limit=5)
    syms = [r["symbol"] for r in results]

    # The two EQUITY entries must come BEFORE the two MUTUAL_FUND entries.
    eq_indices = [i for i, s in enumerate(syms) if s in ("AAPL", "APLE")]
    mf_indices = [i for i, s in enumerate(syms) if s in ("APPLX", "APPIX")]
    assert max(eq_indices) < min(mf_indices), f"equities should rank first: {syms}"


def test_score_instrument_pure() -> None:
    """Unit-test the scoring function directly."""
    score = SchwabProvider._score_instrument
    exact = {"symbol": "AAPL", "description": "APPLE INC", "asset_type": "EQUITY"}
    prefix = {"symbol": "AAPLW", "description": "APPLE WARRANT", "asset_type": "EQUITY"}
    desc = {"symbol": "APLE", "description": "APPLE HOSPITALITY", "asset_type": "EQUITY"}
    mf = {"symbol": "APPLX", "description": "APPLESEED INVESTOR", "asset_type": "MUTUAL_FUND"}

    s_exact = score(exact, "AAPL")
    s_prefix = score(prefix, "AAPL")
    s_desc = score(desc, "APPLE")
    s_mf = score(mf, "APPLE")

    assert s_exact > s_prefix > s_desc > s_mf


@pytest.mark.asyncio
async def test_search_skips_desc_for_single_char(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-letter queries are too broad for description search; skip it."""
    provider = SchwabProvider.__new__(SchwabProvider)
    calls: list[dict] = []

    async def fake_get(path: str, params=None):
        calls.append(dict(params or {}))
        return {"instruments": []}

    monkeypatch.setattr(provider, "_market_data_get", fake_get)
    await provider.search_instruments("N", limit=10)

    projections = [c.get("projection") for c in calls]
    assert "symbol-regex" in projections
    assert "desc-search" not in projections


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty() -> None:
    provider = SchwabProvider.__new__(SchwabProvider)
    assert await provider.search_instruments("") == []
    assert await provider.search_instruments("   ") == []


@pytest.mark.asyncio
async def test_search_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SchwabProvider.__new__(SchwabProvider)

    async def fake_get(path: str, params=None):
        return {"instruments": [
            {"symbol": f"S{i}", "description": f"Sym {i}", "exchange": "NYSE", "assetType": "EQUITY"}
            for i in range(50)
        ]}

    monkeypatch.setattr(provider, "_market_data_get", fake_get)
    results = await provider.search_instruments("S", limit=5)
    assert len(results) == 5


@pytest.mark.asyncio
async def test_search_strips_regex_metachars(monkeypatch: pytest.MonkeyPatch) -> None:
    """User input must not introduce regex metachars into Schwab's `symbol-regex`."""
    provider = SchwabProvider.__new__(SchwabProvider)
    captured: list[dict] = []

    async def fake_get(path: str, params=None):
        captured.append(dict(params or {}))
        return {"instruments": []}

    monkeypatch.setattr(provider, "_market_data_get", fake_get)
    await provider.search_instruments("N.*[evil]$", limit=5)

    sent = captured[0]["symbol"]
    # The only allowed metachars are the anchor `^` and trailing `.*`.
    # Strip those off and assert the rest is alphanumeric only.
    assert sent.startswith("^") and sent.endswith(".*"), sent
    body = sent[1:-2]
    assert body.isalnum(), f"injected metachars survived: {body!r}"


@pytest.mark.asyncio
async def test_search_unit_for_prefix_regex_pure() -> None:
    """Unit-test the prefix regex helper directly: alphanum-only output."""
    assert SchwabProvider._to_prefix_regex("NVD") == "^NVD.*"
    assert SchwabProvider._to_prefix_regex("nvd") == "^NVD.*"
    assert SchwabProvider._to_prefix_regex("") == ""
    assert SchwabProvider._to_prefix_regex("   ") == ""
    assert SchwabProvider._to_prefix_regex("$@!") == ""
    assert SchwabProvider._to_prefix_regex("N.*") == "^N.*"  # dot/star stripped
    assert SchwabProvider._to_prefix_regex("/mnq") == "^/MNQ.*"
    assert SchwabProvider._to_prefix_regex("/") == ""


@pytest.mark.asyncio
async def test_search_provider_error_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network error should NOT bubble — autocomplete must degrade silently."""
    provider = SchwabProvider.__new__(SchwabProvider)

    async def fake_get(path: str, params=None):
        raise RuntimeError("schwab is on fire")

    monkeypatch.setattr(provider, "_market_data_get", fake_get)
    results = await provider.search_instruments("NVD", limit=10)
    assert results == []


# ---------- GET /api/instruments/search route ----------


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with FastAPI lifespan disabled and a fake provider injected."""
    from app.main_api import app
    from app.services.watchlist_service import watchlist_service

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop_lifespan)

    fake_provider = AsyncMock()
    fake_provider.search_instruments = AsyncMock(return_value=[
        {"symbol": "NVDA", "description": "NVIDIA Corp",
         "exchange": "NASDAQ", "asset_type": "EQUITY"},
    ])
    monkeypatch.setattr(watchlist_service, "_provider", fake_provider, raising=False)

    return TestClient(app)


def test_route_returns_results(app_client: TestClient) -> None:
    r = app_client.get("/api/instruments/search", params={"q": "NVD"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "NVD"
    assert len(body["results"]) == 1
    assert body["results"][0]["symbol"] == "NVDA"
    assert body["cached"] is False


def test_route_caches_repeat_query(app_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two identical queries within TTL should only hit the provider once."""
    from app.api import routes_instruments
    routes_instruments._cache.clear()

    from app.services.watchlist_service import watchlist_service
    spy = watchlist_service._provider.search_instruments  # type: ignore[union-attr]

    r1 = app_client.get("/api/instruments/search", params={"q": "AAPL"})
    r2 = app_client.get("/api/instruments/search", params={"q": "AAPL"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True
    assert spy.call_count == 1


def test_route_rejects_empty_query(app_client: TestClient) -> None:
    r = app_client.get("/api/instruments/search", params={"q": ""})
    # FastAPI's min_length=1 should yield 422
    assert r.status_code == 422


def test_route_returns_empty_when_provider_missing(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No provider configured -> route returns 200 with empty results."""
    from app.api import routes_instruments
    routes_instruments._cache.clear()
    from app.services.watchlist_service import watchlist_service
    monkeypatch.setattr(watchlist_service, "_provider", None, raising=False)

    r = app_client.get("/api/instruments/search", params={"q": "TSLA"})
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_route_swallows_provider_exception(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider exception must not surface as HTTP 500."""
    from app.api import routes_instruments
    routes_instruments._cache.clear()
    from app.services.watchlist_service import watchlist_service

    failing = AsyncMock()
    failing.search_instruments = AsyncMock(side_effect=RuntimeError("oops"))
    monkeypatch.setattr(watchlist_service, "_provider", failing, raising=False)

    r = app_client.get("/api/instruments/search", params={"q": "BAD"})
    assert r.status_code == 200
    assert r.json()["results"] == []
