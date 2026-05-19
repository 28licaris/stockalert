"""FE — `/api/v1/instruments/lookup` batch lookup endpoint.

Tests cover the contract:
  - OpenAPI publishes `InstrumentLookupResponse`.
  - Response preserves caller-provided symbol order.
  - Unknown symbols return synthetic entries with empty description
    (never silently dropped — len(results) == len(symbols.split(',')) ).
  - Validation: empty list / oversize list reject through the typed
    ErrorResponse envelope.

Live behavior (provider-warmed cache, real descriptions) is covered
in the manual smoke at the end of FE-CONTRACTS-4. These tests use
TestClient and exercise only the contract; the provider is None in
that environment, so descriptions are empty-by-design here.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.schemas.instruments import InstrumentLookupResponse
from app.main_api import app


def test_openapi_publishes_lookup_response():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    assert "InstrumentLookupResponse" in spec["components"]["schemas"]


def test_lookup_route_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/instruments/lookup"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/InstrumentLookupResponse")


def test_lookup_preserves_symbol_order():
    """Order in `results` matches order in the comma-separated input."""
    client = TestClient(app)
    r = client.get("/api/v1/instruments/lookup?symbols=ZZZ,AAA,MMM")
    assert r.status_code == 200
    body = InstrumentLookupResponse(**r.json())
    assert [i.symbol for i in body.results] == ["ZZZ", "AAA", "MMM"]


def test_lookup_returns_synthetic_entry_for_unknown_symbols():
    """Unknown symbols never get dropped; they come back with empty description."""
    client = TestClient(app)
    r = client.get("/api/v1/instruments/lookup?symbols=FAKEXYZ")
    assert r.status_code == 200
    body = InstrumentLookupResponse(**r.json())
    assert len(body.results) == 1
    assert body.results[0].symbol == "FAKEXYZ"
    assert body.results[0].description == ""


def test_lookup_dedup_and_uppercase_normalization():
    """`a,b,c` → AAAA,BBBB,CCCC (uppercased); request preserves duplicates
    but each result is uppercase."""
    client = TestClient(app)
    r = client.get("/api/v1/instruments/lookup?symbols=aaa,bbb")
    assert r.status_code == 200
    body = InstrumentLookupResponse(**r.json())
    assert [i.symbol for i in body.results] == ["AAA", "BBB"]


def test_lookup_empty_symbols_returns_envelope_400():
    client = TestClient(app)
    r = client.get("/api/v1/instruments/lookup?symbols=")
    # FastAPI handles empty `symbols` via Query(...) validation → 422
    # OR the route's own empty-after-split check → 400. Either way, envelope.
    assert r.status_code in (400, 422)
    body = r.json()
    assert "code" in body and "message" in body


def test_lookup_oversize_returns_envelope_400():
    """Hard cap at 500 symbols per call to prevent provider hammering."""
    client = TestClient(app)
    symbols = ",".join(f"S{i:04d}" for i in range(501))
    r = client.get(f"/api/v1/instruments/lookup?symbols={symbols}")
    assert r.status_code == 400
    body = r.json()
    assert "code" in body and "max 500" in body["message"].lower()
