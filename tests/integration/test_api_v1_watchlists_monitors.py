"""FE-CONTRACTS-3 — type coverage for the watchlist + monitor routes.

Verifies:
  1. OpenAPI publishes the new schemas.
  2. Every route declares the expected response_model.
  3. Live wire shape is unchanged from FE-CONTRACTS-2 (legacy HTML +
     cockpit consumers continue parsing).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.monitors import MonitorActionResponse, MonitorInfo
from app.api.schemas.watchlists import (
    DeleteWatchlistResponse,
    LegacyWatchlistMutationResponse,
    Watchlist,
    WatchlistMembersMutationResponse,
    WatchlistStatus,
)
from app.main_api import app


pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────
# OpenAPI publishes the new schemas
# ─────────────────────────────────────────────────────────────────────


def test_openapi_publishes_watchlist_and_monitor_schemas():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    expected = {
        # Watchlist family
        "Watchlist",
        "CreateWatchlistRequest",
        "RenameWatchlistRequest",
        "SymbolsRequest",
        "DeleteWatchlistResponse",
        "WatchlistMembersMutationResponse",
        "WatchlistSnapshotItem",
        "WatchlistStatus",
        "LegacyWatchlistMutationResponse",
        # Monitor family
        "MonitorInfo",
        "MonitorRequest",
        "MonitorActionResponse",
    }
    missing = expected - set(schemas)
    assert not missing, f"Missing OpenAPI schemas: {missing}"


# ─────────────────────────────────────────────────────────────────────
# Each route declares the expected response_model
# ─────────────────────────────────────────────────────────────────────


def test_v1_watchlists_uses_list_watchlist():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/watchlists"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema.get("type") == "array"
    assert schema["items"]["$ref"].endswith("/Watchlist")


def test_v1_watchlists_post_returns_watchlist():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/watchlists"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]
    assert schema["$ref"].endswith("/Watchlist")


def test_v1_watchlist_single_uses_status():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/watchlist"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema["$ref"].endswith("/WatchlistStatus")


def test_v1_watchlist_add_uses_legacy_mutation_response():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/watchlist/add"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema["$ref"].endswith("/LegacyWatchlistMutationResponse")


def test_v1_monitors_uses_dict_of_monitor_info():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/monitors"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    # `dict[str, MonitorInfo]` → { type: object, additionalProperties: { $ref: .../MonitorInfo } }
    assert schema.get("type") == "object"
    assert schema["additionalProperties"]["$ref"].endswith("/MonitorInfo")


# ─────────────────────────────────────────────────────────────────────
# Wire shape — legacy HTML + cockpit consumers must keep parsing
# ─────────────────────────────────────────────────────────────────────


def test_list_watchlists_wire_shape_preserved():
    client = TestClient(app)
    r = client.get("/api/v1/watchlists")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # When non-empty, every item Watchlist-validates
    for item in body:
        Watchlist(**item)
        # Legacy dashboard reads these specific fields
        for key in ("name", "kind", "is_active", "members", "member_count"):
            assert key in item


def test_legacy_watchlist_status_wire_shape_preserved():
    """`GET /api/v1/watchlist` returns the global stream status. Legacy
    dashboard reads .symbols, .streaming_symbols, .watchlist_count."""
    client = TestClient(app)
    r = client.get("/api/v1/watchlist")
    assert r.status_code == 200
    body = r.json()
    WatchlistStatus(**body)
    for key in (
        "started",
        "provider",
        "symbols",
        "streaming_symbols",
        "watchlist_count",
    ):
        assert key in body


def test_monitors_list_returns_dict_not_list():
    """The legacy dashboard iterates monitors with Object.entries(); the
    bare-dict shape is part of the contract."""
    client = TestClient(app)
    r = client.get("/api/v1/monitors")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict), f"Expected dict, got {type(body).__name__}"
    # When non-empty, every value MonitorInfo-validates
    for v in body.values():
        MonitorInfo(**v)


# ─────────────────────────────────────────────────────────────────────
# Mutation round-trips — create + delete an ephemeral test watchlist
# ─────────────────────────────────────────────────────────────────────


def test_create_then_delete_watchlist_roundtrip():
    """Idempotent CRUD: create → add member → remove member → delete."""
    client = TestClient(app)
    name = "test_fe_contracts_3_ephemeral"

    # cleanup just in case
    client.delete(f"/api/v1/watchlists/{name}")

    # CREATE
    r = client.post(
        "/api/v1/watchlists",
        json={"name": name, "kind": "user", "description": "test"},
    )
    assert r.status_code == 201
    wl = Watchlist(**r.json())
    assert wl.name == name
    assert wl.kind == "user"
    assert wl.is_active is True

    # ADD members
    r = client.post(
        f"/api/v1/watchlists/{name}/members",
        json={"symbols": ["FAKEAA", "FAKEBB"]},
    )
    assert r.status_code == 200
    mutation = WatchlistMembersMutationResponse(**r.json())
    assert "FAKEAA" in mutation.added
    assert "FAKEAA" in mutation.members

    # REMOVE members
    r = client.request(
        "DELETE",
        f"/api/v1/watchlists/{name}/members",
        json={"symbols": ["FAKEAA"]},
    )
    assert r.status_code == 200
    mutation = WatchlistMembersMutationResponse(**r.json())
    assert "FAKEAA" in mutation.removed
    assert "FAKEAA" not in mutation.members

    # DELETE watchlist
    r = client.delete(f"/api/v1/watchlists/{name}")
    assert r.status_code == 200
    deleted = DeleteWatchlistResponse(**r.json())
    assert deleted.deleted == name


def test_default_watchlist_cannot_be_deleted():
    """The 'default' watchlist is the shim target for legacy
    /watchlist routes; deleting it would break back-compat."""
    client = TestClient(app)
    r = client.delete("/api/v1/watchlists/default")
    assert r.status_code == 400
    body = r.json()
    # Goes through the ErrorResponse envelope (FE-CONTRACTS-1)
    assert body["code"] in ("bad_request", "error")


def test_get_unknown_watchlist_returns_envelope_404():
    client = TestClient(app)
    r = client.get("/api/v1/watchlists/this-name-does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"


def test_legacy_watchlist_add_remove_wire_shape():
    """The legacy single-watchlist routes return `LegacyWatchlistMutationResponse`
    with `added`/`symbols` (NOT `members`). symbol.html depends on this."""
    client = TestClient(app)

    # Add to default
    r = client.post("/api/v1/watchlist/add", json={"symbols": ["FAKETEST"]})
    assert r.status_code == 200
    body = LegacyWatchlistMutationResponse(**r.json())
    assert "FAKETEST" in body.added
    assert "FAKETEST" in body.symbols

    # Remove from default
    r = client.post("/api/v1/watchlist/remove", json={"symbols": ["FAKETEST"]})
    assert r.status_code == 200
    body = LegacyWatchlistMutationResponse(**r.json())
    assert "FAKETEST" in body.removed
    assert "FAKETEST" not in body.symbols
