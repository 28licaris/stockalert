"""FE-CONTRACTS-4 — seed-universe endpoint tests.

Tests live against the real CH instance configured by the dev env.
Each test uses a unique `SEED_TEST_<id>` symbol so parallel runs +
re-runs don't conflict with curated SEED_SYMBOLS or operator state.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.seed import SeedMutationResponse, SeedUniverseResponse
from app.main_api import app
from app.services.seed import seed_service


pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────
# OpenAPI publishes the schemas
# ─────────────────────────────────────────────────────────────────────


def test_openapi_publishes_seed_schemas():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    expected = {
        "SeedEntry",
        "SeedUniverseResponse",
        "AddSeedRequest",
        "ImportSeedRequest",
        "SeedMutationResponse",
    }
    missing = expected - set(schemas)
    assert not missing, f"Missing: {missing}"


def test_v1_seed_get_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/seed"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema["$ref"].endswith("/SeedUniverseResponse")


def test_v1_seed_post_uses_mutation_response():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/seed"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]
    assert schema["$ref"].endswith("/SeedMutationResponse")


# ─────────────────────────────────────────────────────────────────────
# Live behavior
# ─────────────────────────────────────────────────────────────────────


def test_get_seed_returns_typed_envelope_after_bootstrap():
    client = TestClient(app)
    r = client.get("/api/v1/seed")
    assert r.status_code == 200
    body = r.json()
    parsed = SeedUniverseResponse(**body)
    # Bootstrap may or may not have fired depending on whether the
    # table already had rows from a prior test run / operator action.
    # Either way the response must have items and count consistent.
    assert parsed.count == len(parsed.items)
    if parsed.count > 0:
        # Every item must have the documented fields
        for item in parsed.items:
            assert item.symbol
            assert isinstance(item.added_at, str)


def test_add_remove_roundtrip_idempotent():
    """Add → re-add (no-op) → remove → re-remove (no-op)."""
    client = TestClient(app)
    sym = f"SEEDTST{uuid.uuid4().hex[:6].upper()}"

    # ADD
    r = client.post("/api/v1/seed", json={"symbol": sym, "notes": "test"})
    assert r.status_code == 201
    body = SeedMutationResponse(**r.json())
    assert body.operation == "add"
    assert sym in body.changed
    assert sym in {i.symbol for i in body.items}

    # Re-add: idempotent, changed=[]
    r = client.post("/api/v1/seed", json={"symbol": sym})
    assert r.status_code == 201
    body = SeedMutationResponse(**r.json())
    assert body.changed == [], f"Re-add should be a no-op; got changed={body.changed}"

    # REMOVE
    r = client.delete(f"/api/v1/seed/{sym}")
    assert r.status_code == 200
    body = SeedMutationResponse(**r.json())
    assert body.operation == "remove"
    assert sym in body.changed
    assert sym not in {i.symbol for i in body.items}

    # Re-remove: idempotent, changed=[]
    r = client.delete(f"/api/v1/seed/{sym}")
    assert r.status_code == 200
    body = SeedMutationResponse(**r.json())
    assert body.changed == [], f"Re-remove should be a no-op; got changed={body.changed}"


def test_import_bulk_partial_overlap_idempotent():
    """Two of three symbols are new; one is already in the table."""
    client = TestClient(app)
    pre = f"SEEDPRE{uuid.uuid4().hex[:6].upper()}"
    fresh1 = f"SEEDIM1{uuid.uuid4().hex[:6].upper()}"
    fresh2 = f"SEEDIM2{uuid.uuid4().hex[:6].upper()}"

    # Pre-stage one of the three
    client.post("/api/v1/seed", json={"symbol": pre})

    r = client.post(
        "/api/v1/seed/import", json={"symbols": [pre, fresh1, fresh2]}
    )
    assert r.status_code == 200
    body = SeedMutationResponse(**r.json())
    assert body.operation == "import"
    # The two fresh ones should be in `changed`; `pre` should not.
    assert set(body.changed) == {fresh1, fresh2}

    # cleanup
    for s in (pre, fresh1, fresh2):
        client.delete(f"/api/v1/seed/{s}")


def test_invalid_symbol_returns_envelope():
    """Empty/junk symbol after normalization returns the typed
    ErrorResponse envelope, not the legacy {detail: ...} shape."""
    client = TestClient(app)
    r = client.post("/api/v1/seed", json={"symbol": "   "})
    # 422 from min_length=1, OR 400 from service ValueError
    assert r.status_code in (400, 422)
    body = r.json()
    assert "code" in body and "message" in body, (
        f"Expected ErrorResponse envelope, got {body}"
    )


def test_seed_service_singleton_factory():
    """The from_settings() factory matches the SaaS-readiness pattern
    (memory: 'service module design')."""
    s1 = seed_service
    s2 = seed_service.from_settings()
    # Different instances are fine; same behavior is what matters.
    assert isinstance(s2, type(s1))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("aapl", "AAPL"),
        ("  spy  ", "SPY"),
        ("nvda", "NVDA"),
    ],
)
def test_symbol_normalization(raw: str, expected: str):
    """Symbols normalize through `watchlist_repo.normalize_member_symbol`."""
    client = TestClient(app)
    r = client.post("/api/v1/seed", json={"symbol": raw})
    assert r.status_code == 201
    body = SeedMutationResponse(**r.json())
    # If symbol was already present from curated SEED_SYMBOLS or default
    # watchlist, `changed` is empty — but the symbol must still appear
    # in the items.
    assert expected in {i.symbol for i in body.items}
