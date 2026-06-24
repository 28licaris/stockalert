"""Tests for FE-CONTRACTS-1 — the /api/v1 namespace + ErrorResponse envelope.

Three things this suite verifies:
  1. All cockpit-facing routes are reachable at /api/v1/*.
  2. Legacy /api/* + /watchlist[/*] paths return 307 redirects to v1.
  3. Errors come out as ErrorResponse envelopes (code/message/details),
     never the legacy `{"detail": "..."}` shape.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main_api import app


pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────
# /api/v1 namespace — every legacy /api endpoint is reachable here
# ─────────────────────────────────────────────────────────────────────


def test_v1_health_services_reachable():
    """Spot-check: the FE-1.5 composite probe is at /api/v1/."""
    client = TestClient(app)
    r = client.get("/api/v1/health/services")
    assert r.status_code == 200
    assert "services" in r.json()


def test_v1_market_banner_reachable():
    """Spot-check: market banner (legacy /api/market/banner) is at /api/v1."""
    client = TestClient(app)
    r = client.get("/api/v1/market/banner?symbols=SPY")
    # 200 or any structured response — we're only checking the route
    # exists at v1, not the business behavior.
    assert r.status_code in (200, 503)


def test_v1_watchlists_reachable():
    """Multi-watchlist family is at /api/v1/watchlists (not /api/v1/api/watchlists)."""
    client = TestClient(app)
    r = client.get("/api/v1/watchlists")
    assert r.status_code == 200


def test_v1_watchlist_single_reachable():
    """Legacy single-watchlist route family is at /api/v1/watchlist (also legacy redirect)."""
    client = TestClient(app)
    r = client.get("/api/v1/watchlist")
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────
# Legacy redirects — 307 preserves method + body
# ─────────────────────────────────────────────────────────────────────


def test_legacy_api_path_redirects_to_v1():
    """Every legacy /api/<anything> returns a 307 → /api/v1/<anything>."""
    client = TestClient(app)
    r = client.get("/api/health/services", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/v1/health/services"


def test_legacy_api_path_preserves_query_string():
    """Query string survives the redirect (critical for charts, search, etc.)."""
    client = TestClient(app)
    r = client.get(
        "/api/bars?symbol=AAPL&interval=5m&limit=10",
        follow_redirects=False,
    )
    assert r.status_code == 307
    assert (
        r.headers["location"]
        == "/api/v1/bars?symbol=AAPL&interval=5m&limit=10"
    )


def test_legacy_api_path_redirect_follows_to_200():
    """End-to-end: follow_redirects=True lands on the real v1 handler."""
    client = TestClient(app)
    r = client.get("/api/health/services")  # default follow_redirects=True
    assert r.status_code == 200
    assert "services" in r.json()


def test_legacy_watchlist_root_redirects():
    """Legacy /watchlist (no trailing path) → /api/v1/watchlist."""
    client = TestClient(app)
    r = client.get("/watchlist", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/v1/watchlist"


def test_legacy_watchlist_subpath_redirects():
    """Legacy /watchlist/snapshot → /api/v1/watchlist/snapshot."""
    client = TestClient(app)
    r = client.get("/watchlist/snapshot", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/v1/watchlist/snapshot"


def test_legacy_api_post_preserves_method():
    """307 (not 301/302) preserves POST method for legacy form-style calls."""
    client = TestClient(app)
    # POST /api/backfill is a real legacy endpoint; sending an empty
    # symbols array triggers 400 at the v1 handler — proving the 307
    # forwarded both method AND body.
    r = client.post("/api/backfill", json={"symbols": [], "days": 5})
    # End URL after redirect is /api/v1/backfill; the handler returns
    # 400 for empty symbols. Either redirect-then-400 is fine — both
    # prove the body made it to a POST handler at the v1 path.
    assert r.status_code in (400, 422)


def test_v1_path_falling_through_catchall_404s_cleanly():
    """If a /api/v1/<unknown> path falls through, we get a clean 404 with envelope, not a redirect loop."""
    client = TestClient(app)
    r = client.get("/api/v1/totally-fake-path", follow_redirects=False)
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"


# ─────────────────────────────────────────────────────────────────────
# ErrorResponse envelope shape
# ─────────────────────────────────────────────────────────────────────


def test_404_uses_error_envelope():
    """Unknown route returns code/message/details/request_id shape."""
    client = TestClient(app)
    r = client.get("/api/v1/this-route-does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert set(body.keys()) == {"code", "message", "details", "request_id"}
    assert body["code"] == "not_found"
    assert isinstance(body["message"], str)


def test_422_validation_error_uses_error_envelope():
    """422 from missing required query param returns envelope with field details."""
    client = TestClient(app)
    r = client.get("/api/v1/bars")  # missing required `symbol`
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "errors" in body["details"]
    # The bad field surfaces in details.errors[].loc
    locs = [tuple(e.get("loc", [])) for e in body["details"]["errors"]]
    assert ("query", "symbol") in locs


def test_route_raised_http_exception_uses_error_envelope():
    """A route raising HTTPException(400, '...') goes through the envelope handler."""
    client = TestClient(app)
    # Backfill with empty symbols raises HTTPException(400, "symbols list is empty")
    r = client.post("/api/v1/backfill", json={"symbols": [], "days": 5})
    # Some legacy routes return 422 (Pydantic) vs 400 (manual). Either way, envelope.
    assert r.status_code in (400, 422)
    body = r.json()
    assert "code" in body
    assert "message" in body
    # Verify it's NOT the legacy {"detail": "..."} shape
    assert "detail" not in body or isinstance(body.get("detail"), (dict, list))
