"""FE-CONTRACTS-2 — verify the five newly typed endpoints declare their
response_model AND return the documented shape.

These tests use TestClient against the real app (no business mocks) so
they exercise the actual Pydantic validation FastAPI applies on the way
out. A response shape that no longer matches the model would 500 here.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main_api import app
from app.api.schemas.bars import Bar
from app.api.schemas.signals import Signal
from app.api.schemas.instruments import InstrumentSearchResponse
from app.api.schemas.market import MarketBannerResponse, MoversResponse


# ─────────────────────────────────────────────────────────────────────
# OpenAPI now publishes the schemas for cockpit codegen.
# ─────────────────────────────────────────────────────────────────────


def test_openapi_publishes_all_five_schemas():
    """Every model added in FE-CONTRACTS-2 lands in /openapi.json."""
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    expected = {
        "Bar",
        "Signal",
        "InstrumentMatch",
        "InstrumentSearchResponse",
        "BannerItem",
        "MarketBannerResponse",
        "Mover",
        "MoversResponse",
    }
    missing = expected - set(schemas)
    assert not missing, f"Missing OpenAPI schemas: {missing}"


def test_v1_bars_uses_response_model():
    """The route is declared with response_model=list[Bar]."""
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/bars"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]
    # response_model=list[Bar] surfaces as { type: array, items: { $ref: .../Bar } }
    assert schema_ref.get("type") == "array"
    assert schema_ref["items"]["$ref"].endswith("/Bar")


def test_v1_signals_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/signals"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema_ref.get("type") == "array"
    assert schema_ref["items"]["$ref"].endswith("/Signal")


def test_v1_instruments_search_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/instruments/search"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"].endswith("/InstrumentSearchResponse")


def test_v1_market_banner_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/market/banner"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"].endswith("/MarketBannerResponse")


def test_v1_movers_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/v1/movers"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"].endswith("/MoversResponse")


# ─────────────────────────────────────────────────────────────────────
# Live behavior — wire shape preserved for legacy HTML consumers
# ─────────────────────────────────────────────────────────────────────


def test_v1_bars_returns_bare_list_of_bar_dicts():
    """Legacy symbol.html iterates the bare list and reads `b.close` etc.
    Adding optional vwap/trade_count/source is non-breaking; renaming or
    wrapping would break it."""
    client = TestClient(app)
    r = client.get("/api/v1/bars?symbol=AAPL&interval=5m&limit=2")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list), "Bars must be a bare list, not wrapped"
    if data:
        for required in ("ts", "open", "high", "low", "close", "volume"):
            assert required in data[0]
        # Roundtrip through the Pydantic model
        Bar(**data[0])


def test_v1_signals_returns_bare_list_of_signal_dicts():
    client = TestClient(app)
    r = client.get("/api/v1/signals?symbol=AAPL&limit=2")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # Empty list is valid (no recent signals). When non-empty, model-validate.
    for item in data:
        Signal(**item)


def test_v1_market_banner_returns_envelope_with_items_and_errors():
    """Legacy dashboard reads `as_of`, `items`, `errors`. Field set is locked."""
    client = TestClient(app)
    r = client.get("/api/v1/market/banner?symbols=$SPX,$DJI")
    assert r.status_code == 200
    body = r.json()
    MarketBannerResponse(**body)  # roundtrip
    assert {"as_of", "provider", "items", "errors"}.issubset(body.keys())


def test_v1_movers_returns_full_dashboard_shape():
    """Legacy dashboard reads `movers`, `indexes`, `upstream_count`,
    `per_index_counts`, `filtered_out`, `fetched_at`. All required."""
    client = TestClient(app)
    r = client.get("/api/v1/movers?index=%24SPX&limit=2")
    assert r.status_code == 200
    body = r.json()
    MoversResponse(**body)  # roundtrip
    assert {
        "index",
        "indexes",
        "provider",
        "sort",
        "frequency",
        "count",
        "upstream_count",
        "filtered_out",
        "per_index_counts",
        "fetched_at",
        "movers",
    }.issubset(body.keys())


def test_v1_instruments_search_returns_query_results_cached():
    client = TestClient(app)
    r = client.get("/api/v1/instruments/search?q=NVD&limit=3")
    assert r.status_code == 200
    body = r.json()
    InstrumentSearchResponse(**body)
    assert body["query"] == "NVD"
    assert isinstance(body["results"], list)
    assert isinstance(body["cached"], bool)
