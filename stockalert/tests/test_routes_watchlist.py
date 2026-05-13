"""
Integration tests for /api/watchlists routes (Phase 1.3).

Spins up the FastAPI app via TestClient and exercises every CRUD path
through the HTTP layer. Uses a unique `__test_api_wl_` prefix so the
real `default` watchlist is never touched.

Requires ClickHouse to be running (docker-compose up clickhouse).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import watchlist_repo
from app.db.client import get_client


TEST_PREFIX = "__test_api_wl_"


def _wipe(name: str) -> None:
    """Hard-delete a test watchlist + its members. Refuses non-test names."""
    if not name.startswith("__test_"):
        raise ValueError(f"_wipe refused non-test name {name!r}")
    client = get_client()
    client.command(
        "ALTER TABLE watchlists DELETE WHERE name = {n:String}",
        parameters={"n": name},
    )
    client.command(
        "ALTER TABLE watchlist_members DELETE WHERE watchlist_name = {n:String}",
        parameters={"n": name},
    )


@pytest.fixture(scope="module")
def app_client(clickhouse_ready):
    """Build a TestClient against the FastAPI app, skipping the real lifespan.

    The production lifespan starts the watchlist service (which spawns the
    Schwab streamer and fires auto-backfills) and the OHLCV batch writer.
    None of that is needed for route-level tests, and the streamer would
    fail to log in without credentials. We swap the lifespan for a no-op
    so TestClient still works.
    """
    from contextlib import asynccontextmanager
    from app.main_api import app

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as c:
        yield c


@pytest.fixture
def wl_name():
    """Yield a unique watchlist name and clean up after the test."""
    name = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    yield name
    _wipe(name)


# ----- list / create -----


def test_list_endpoint_returns_default(app_client) -> None:
    r = app_client.get("/api/watchlists")
    assert r.status_code == 200
    bodies = r.json()
    assert isinstance(bodies, list)
    names = [b["name"] for b in bodies]
    assert "default" in names, "default watchlist must always exist"
    # Every entry has the canonical shape
    for b in bodies:
        assert "name" in b and "kind" in b and "is_active" in b
        assert "members" in b and "member_count" in b
        assert b["member_count"] == len(b["members"])


def test_create_watchlist_succeeds(app_client, wl_name: str) -> None:
    r = app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user", "description": "test"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == wl_name
    assert body["kind"] == "user"
    assert body["description"] == "test"
    assert body["members"] == []
    assert body["member_count"] == 0


def test_create_watchlist_rejects_invalid_kind(app_client, wl_name: str) -> None:
    r = app_client.post("/api/watchlists", json={"name": wl_name, "kind": "totally-bogus"})
    assert r.status_code == 400


def test_create_watchlist_rejects_empty_name(app_client) -> None:
    r = app_client.post("/api/watchlists", json={"name": "", "kind": "user"})
    # Pydantic validation: 422
    assert r.status_code == 422


# ----- get / members -----


def test_get_single_watchlist(app_client, wl_name: str) -> None:
    app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user"})
    r = app_client.get(f"/api/watchlists/{wl_name}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == wl_name
    assert body["members"] == []


def test_get_missing_watchlist_404(app_client) -> None:
    r = app_client.get(f"/api/watchlists/{TEST_PREFIX}does_not_exist")
    assert r.status_code == 404


def test_add_and_list_members(app_client, wl_name: str) -> None:
    app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user"})
    r = app_client.post(
        f"/api/watchlists/{wl_name}/members",
        json={"symbols": ["aapl", "MSFT", "googl"]},
    )
    assert r.status_code == 200
    body = r.json()
    # Symbols are normalized to uppercase by the repo
    assert sorted(body["added"]) == ["AAPL", "GOOGL", "MSFT"]
    assert sorted(body["members"]) == ["AAPL", "GOOGL", "MSFT"]

    # Re-adding is idempotent
    r2 = app_client.post(
        f"/api/watchlists/{wl_name}/members",
        json={"symbols": ["AAPL"]},
    )
    assert r2.status_code == 200
    assert r2.json()["added"] == [], "AAPL already there → no newly-added"


def test_remove_members(app_client, wl_name: str) -> None:
    app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user"})
    app_client.post(f"/api/watchlists/{wl_name}/members", json={"symbols": ["AAPL", "MSFT"]})
    r = app_client.request(
        "DELETE",
        f"/api/watchlists/{wl_name}/members",
        json={"symbols": ["AAPL"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["removed"] == ["AAPL"]
    assert body["members"] == ["MSFT"]


def test_members_endpoint_lists_active_only(app_client, wl_name: str) -> None:
    app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user"})
    app_client.post(f"/api/watchlists/{wl_name}/members", json={"symbols": ["AAPL", "TSLA"]})
    r = app_client.get(f"/api/watchlists/{wl_name}/members")
    assert r.status_code == 200
    assert sorted(r.json()) == ["AAPL", "TSLA"]


# ----- rename / delete -----


def test_rename_watchlist(app_client, wl_name: str) -> None:
    new_name = f"{TEST_PREFIX}renamed_{uuid.uuid4().hex[:6]}"
    try:
        app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user"})
        app_client.post(f"/api/watchlists/{wl_name}/members", json={"symbols": ["AAPL"]})
        r = app_client.patch(f"/api/watchlists/{wl_name}", json={"new_name": new_name})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == new_name
        assert body["members"] == ["AAPL"], "members must carry over to the renamed watchlist"
        # Old name is gone
        assert app_client.get(f"/api/watchlists/{wl_name}").status_code == 404
    finally:
        _wipe(new_name)


def test_cannot_rename_default(app_client) -> None:
    r = app_client.patch("/api/watchlists/default", json={"new_name": "renamed"})
    assert r.status_code == 400


def test_delete_watchlist(app_client, wl_name: str) -> None:
    app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user"})
    r = app_client.delete(f"/api/watchlists/{wl_name}")
    assert r.status_code == 200
    assert r.json() == {"deleted": wl_name}
    # Subsequent reads should 404
    assert app_client.get(f"/api/watchlists/{wl_name}").status_code == 404


def test_cannot_delete_default(app_client) -> None:
    r = app_client.delete("/api/watchlists/default")
    assert r.status_code == 400


def test_delete_missing_404(app_client) -> None:
    r = app_client.delete(f"/api/watchlists/{TEST_PREFIX}never_existed")
    assert r.status_code == 404


# ----- snapshot -----


def test_snapshot_returns_member_rows(app_client, wl_name: str) -> None:
    # Use a fake symbol that will not be in ohlcv_1m so we get the "no data yet" shape.
    # Repo uppercases symbols on insert, so we use an already-uppercase value.
    fake_sym = f"__TEST_API_{uuid.uuid4().hex[:6].upper()}"
    app_client.post("/api/watchlists", json={"name": wl_name, "kind": "user"})
    app_client.post(f"/api/watchlists/{wl_name}/members", json={"symbols": [fake_sym]})
    r = app_client.get(f"/api/watchlists/{wl_name}/snapshot")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == fake_sym
    assert row["bar_count"] == 0
    assert row["ts"] is None


def test_snapshot_missing_watchlist_404(app_client) -> None:
    r = app_client.get(f"/api/watchlists/{TEST_PREFIX}nope/snapshot")
    assert r.status_code == 404


# ----- legacy routes still work -----


def test_legacy_watchlist_routes_still_work(app_client) -> None:
    """The old /watchlist family must keep working — the dashboard hasn't migrated yet."""
    r = app_client.get("/watchlist")
    assert r.status_code == 200
    body = r.json()
    assert "symbols" in body and "started" in body and "provider" in body

    r2 = app_client.get("/watchlist/snapshot")
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)
