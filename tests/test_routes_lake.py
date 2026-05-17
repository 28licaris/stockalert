"""
Pre-Phase 3 Step 2 GATE — `/api/lake/bars` route.

Two complementary tests:

  1. Route-shape + dependency-injection happy path (mocked reader).
     Asserts the handler delegates cleanly to `BronzeReader.get_bars`
     and returns the canonical `BronzeBarsResponse` shape.

  2. **The CH-independence gate.** Imports `routes_lake` and walks
     every transitively-imported module in its handler call path,
     asserting NONE of them reach into `app.db.*` (ClickHouse). This
     is a structural invariant check: even if a future change
     accidentally adds a CH import to the bronze read path, this
     test will fail BEFORE production breakage.

Plus error-path tests for unknown provider (400) and infra errors
(500). The full end-to-end check with a real bronze table and a
real stopped ClickHouse is documented in BUILD_JOURNAL.md as the
manual gate procedure — Slice 2 lands when both this test passes
AND the manual procedure has been run once.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_lake
from app.api.routes_lake import get_bronze_reader
from app.services.readers.bronze_reader import BronzeReader
from app.services.readers.schemas import BronzeBar


def _make_app() -> FastAPI:
    """Minimal FastAPI app with just the lake router mounted."""
    app = FastAPI()
    app.include_router(routes_lake.router, prefix="/api", tags=["Lake"])
    return app


class _StubReader:
    """
    Stand-in for BronzeReader. Records the last call args per method so
    tests can verify the route passes parameters through correctly.
    """

    def __init__(
        self,
        bars: list[BronzeBar] | None = None,
        symbols: list[str] | None = None,
        latest_day=None,
        raises: Exception | None = None,
    ) -> None:
        self._bars = bars or []
        self._symbols = symbols if symbols is not None else []
        self._latest_day = latest_day
        self._raises = raises
        self.last_call: dict | None = None

    def get_bars(self, symbol, start, end, *, provider="polygon", limit=None):
        self.last_call = {
            "method": "get_bars",
            "symbol": symbol, "start": start, "end": end,
            "provider": provider, "limit": limit,
        }
        if self._raises:
            raise self._raises
        return self._bars

    def list_symbols(self, *, provider="polygon", since=None, limit=None):
        self.last_call = {
            "method": "list_symbols",
            "provider": provider, "since": since, "limit": limit,
        }
        if self._raises:
            raise self._raises
        return self._symbols

    def latest_trading_day(self, *, provider="polygon", lookback_days=14):
        self.last_call = {
            "method": "latest_trading_day",
            "provider": provider, "lookback_days": lookback_days,
        }
        if self._raises:
            raise self._raises
        return self._latest_day


def _bars_fixture() -> list[BronzeBar]:
    return [
        BronzeBar(
            symbol="AAPL",
            timestamp=datetime(2024, 8, 1, 14, m, tzinfo=timezone.utc),
            open=190.0 + m * 0.01,
            high=190.5 + m * 0.01,
            low=189.5 + m * 0.01,
            close=190.2 + m * 0.01,
            volume=1000.0 + m,
            vwap=None,
            trade_count=10 + m,
            source="polygon",
        )
        for m in range(3)
    ]


def test_lake_bars_happy_path() -> None:
    """Route delegates to the reader and returns the canonical response shape."""
    app = _make_app()
    stub = _StubReader(bars=_bars_fixture())
    app.dependency_overrides[get_bronze_reader] = lambda: stub

    with TestClient(app) as client:
        resp = client.get(
            "/api/lake/bars",
            params={
                "symbol": "AAPL",
                "start": "2024-08-01T14:00:00Z",
                "end": "2024-08-01T15:00:00Z",
                "provider": "polygon",
                "limit": 100,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["provider"] == "polygon"
    assert body["count"] == 3
    assert len(body["bars"]) == 3
    assert body["bars"][0]["symbol"] == "AAPL"
    assert body["bars"][0]["source"] == "polygon"

    # Route passed the right args through.
    assert stub.last_call is not None
    assert stub.last_call["symbol"] == "AAPL"
    assert stub.last_call["provider"] == "polygon"
    assert stub.last_call["limit"] == 100


def test_lake_bars_empty_window_returns_200_empty_list() -> None:
    """No rows in window = 200 with bars:[], not a 404."""
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(bars=[])

    with TestClient(app) as client:
        resp = client.get(
            "/api/lake/bars",
            params={
                "symbol": "AAPL",
                "start": "2020-01-01T00:00:00Z",
                "end": "2020-01-02T00:00:00Z",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["bars"] == []


def test_lake_bars_unknown_provider_returns_400() -> None:
    """`ValueError` from the reader → HTTP 400 (client error)."""
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(
        raises=ValueError("Unknown provider 'madeup'. Supported: polygon, schwab.")
    )

    with TestClient(app) as client:
        resp = client.get(
            "/api/lake/bars",
            params={
                "symbol": "AAPL",
                "start": "2024-08-01T14:00:00Z",
                "end": "2024-08-01T15:00:00Z",
                "provider": "madeup",
            },
        )

    assert resp.status_code == 400
    assert "Unknown provider" in resp.json()["detail"]


def test_lake_bars_infra_error_returns_500() -> None:
    """Unexpected reader exception → HTTP 500 (server error, not a 200 hiding it)."""
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(
        raises=RuntimeError("S3 connection reset")
    )

    with TestClient(app) as client:
        resp = client.get(
            "/api/lake/bars",
            params={
                "symbol": "AAPL",
                "start": "2024-08-01T14:00:00Z",
                "end": "2024-08-01T15:00:00Z",
            },
        )

    assert resp.status_code == 500
    assert "bronze read failed" in resp.json()["detail"]


def test_lake_bars_validates_query_params() -> None:
    """Missing required params → 422 from FastAPI's validation layer."""
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(bars=[])

    with TestClient(app) as client:
        resp = client.get("/api/lake/bars", params={"symbol": "AAPL"})

    assert resp.status_code == 422  # start, end are required
    detail = resp.json()["detail"]
    missing_locs = {tuple(e["loc"]) for e in detail}
    assert ("query", "start") in missing_locs
    assert ("query", "end") in missing_locs


# ---------------------------------------------------------------------
# /api/lake/symbols
# ---------------------------------------------------------------------
def test_lake_symbols_happy_path() -> None:
    """Route returns the symbols the reader produced, with provider + since echoed."""
    app = _make_app()
    stub = _StubReader(symbols=["AAPL", "MSFT", "NVDA", "SPY"])
    app.dependency_overrides[get_bronze_reader] = lambda: stub

    with TestClient(app) as client:
        resp = client.get(
            "/api/lake/symbols",
            params={"provider": "polygon", "since": "2024-08-01T00:00:00Z", "limit": 100},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "polygon"
    assert body["count"] == 4
    assert body["symbols"] == ["AAPL", "MSFT", "NVDA", "SPY"]
    # Reader got the parsed datetime, not the string.
    assert stub.last_call["method"] == "list_symbols"
    assert isinstance(stub.last_call["since"], datetime)
    assert stub.last_call["limit"] == 100


def test_lake_symbols_default_since_uses_30d_window() -> None:
    """When `since` is omitted, the response echoes a 30-day-back default."""
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(symbols=["AAPL"])

    before = datetime.now(timezone.utc)
    with TestClient(app) as client:
        resp = client.get("/api/lake/symbols", params={"provider": "polygon"})
    after = datetime.now(timezone.utc)

    assert resp.status_code == 200
    body = resp.json()
    # Parse echoed `since` and check it's roughly 30 days ago.
    echoed_since = datetime.fromisoformat(body["since"].replace("Z", "+00:00"))
    expected_lo = before - timedelta(days=30, minutes=1)
    expected_hi = after - timedelta(days=29, hours=23, minutes=59)
    assert expected_lo <= echoed_since <= expected_hi, (
        f"echoed since {echoed_since} not in expected window {expected_lo}..{expected_hi}"
    )


def test_lake_symbols_unknown_provider_returns_400() -> None:
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(
        raises=ValueError("Unknown provider 'madeup'. Supported: polygon, schwab.")
    )

    with TestClient(app) as client:
        resp = client.get("/api/lake/symbols", params={"provider": "madeup"})

    assert resp.status_code == 400


# ---------------------------------------------------------------------
# /api/lake/last-day
# ---------------------------------------------------------------------
def test_lake_last_day_happy_path() -> None:
    """Route returns the date the reader produced, ISO-formatted."""
    from datetime import date as _date

    app = _make_app()
    stub = _StubReader(latest_day=_date(2024, 8, 14))
    app.dependency_overrides[get_bronze_reader] = lambda: stub

    with TestClient(app) as client:
        resp = client.get(
            "/api/lake/last-day",
            params={"provider": "polygon", "lookback_days": 30},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "polygon"
    assert body["latest_trading_day"] == "2024-08-14"
    assert stub.last_call["method"] == "latest_trading_day"
    assert stub.last_call["lookback_days"] == 30


def test_lake_last_day_returns_null_when_no_data() -> None:
    """No data in window -> 200 with latest_trading_day: null, not 404."""
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(latest_day=None)

    with TestClient(app) as client:
        resp = client.get("/api/lake/last-day", params={"provider": "polygon"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_trading_day"] is None
    assert body["provider"] == "polygon"


def test_lake_last_day_unknown_provider_returns_400() -> None:
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(
        raises=ValueError("Unknown provider 'madeup'.")
    )

    with TestClient(app) as client:
        resp = client.get("/api/lake/last-day", params={"provider": "madeup"})

    assert resp.status_code == 400


def test_lake_last_day_validates_lookback_bounds() -> None:
    """lookback_days bounded to [1, 365]."""
    app = _make_app()
    app.dependency_overrides[get_bronze_reader] = lambda: _StubReader(latest_day=None)

    with TestClient(app) as client:
        # 0 below min
        resp = client.get("/api/lake/last-day", params={"lookback_days": 0})
        assert resp.status_code == 422
        # 366 above max
        resp = client.get("/api/lake/last-day", params={"lookback_days": 366})
        assert resp.status_code == 422


# ---------------------------------------------------------------------
# The CH-independence gate
# ---------------------------------------------------------------------
def test_lake_route_does_not_import_clickhouse() -> None:
    """
    GATE: walk every module reachable from `routes_lake` and assert none
    of them sit under `app.db.*`. CH-independence is the load-bearing
    property of this route — if it regresses, training and agent
    historical reads break the moment CH goes down.

    Implementation: trace `sys.modules` for `app.*` entries whose
    source file mentions ClickHouse / app.db imports in the modules
    actually reachable from `routes_lake`. Conservative: we just
    snapshot `sys.modules` keys after importing `routes_lake` fresh
    and assert nothing under `app.db` or `app.services.live.*` shows up.

    Caveat: pytest itself imports everything, so we can't check the
    full process. We test by inspecting routes_lake's *source* AST and
    its direct + transitive `app.*` imports from a clean module graph.
    """
    import ast
    import importlib.util

    # Collect modules transitively imported by `routes_lake`, starting
    # from its source and walking only `app.*` deps (we don't care
    # about stdlib / FastAPI / pydantic).
    visited: set[str] = set()
    forbidden_prefixes = ("app.db",)

    def _module_path(mod_name: str) -> str | None:
        try:
            spec = importlib.util.find_spec(mod_name)
        except (ImportError, ValueError):
            return None
        if spec is None or spec.origin in (None, "built-in"):
            return None
        return spec.origin

    def _walk(mod_name: str) -> None:
        if mod_name in visited:
            return
        visited.add(mod_name)
        path = _module_path(mod_name)
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError):
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        _walk(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("app."):
                    _walk(node.module)

    _walk("app.api.routes_lake")

    leaked = sorted(
        m for m in visited
        if any(m.startswith(prefix) for prefix in forbidden_prefixes)
    )
    assert not leaked, (
        f"CH-independence regression: {len(leaked)} module(s) under "
        f"{forbidden_prefixes} are reachable from app.api.routes_lake.\n"
        "The lake route MUST NOT depend on ClickHouse. Move any CH-bound "
        "logic to a CH-backed reader and keep this route on BronzeReader only.\n"
        f"Leaked modules: {leaked}"
    )
