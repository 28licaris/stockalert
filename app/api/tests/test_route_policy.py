"""Role policy: every route in the REAL app is classified, and non-operators are denied.

The runtime policy in ``app/api/auth_middleware.py`` is default-deny for
non-operators (anything not matched by ``CUSTOMER_RULES`` is operator-only). That
alone fails closed, but silently: a new route a customer page needs would just
403. This module makes the classification explicit and loud:

* **Every route must be classified.** Each (method, path template) of the real
  ``app.main_api.app`` has to be public (allowlisted), a reviewed ungated path,
  matched by exactly one ``CUSTOMER_RULES`` entry, or matched by exactly one
  ``OPERATOR_RULES`` entry. A new route in none of them fails here, with its name.
* **No stale or ambiguous rules**: each rule matches a real route; no route is
  both customer and operator.
* **Operator-gated routes stay operator**: a route carrying ``require_operator``
  can never be opened up by a customer rule.
* **Behaviour**: with auth on, a signed-in non-operator gets 403 (websocket:
  close 1008) on every operator-tier route, before any handler runs.

No Cognito, PostgreSQL or ClickHouse: the identity service is faked and the
operator-tier requests never reach a handler.
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import auth_dependencies
from app.api.auth_middleware import (
    CUSTOMER_RULES,
    OPERATOR_RULES,
    classify_path,
    policy_tier,
)
from app.config import settings
from app.services.identity.schemas import Principal, Role

SESSION_TOKEN = "session-token"
REAL_GET_IDENTITY_SERVICE = auth_dependencies.get_identity_service

# Paths outside the gated prefixes (/api/, /cockpit/, /ws/, /mcp) that the session
# gate deliberately does not touch. Reviewed: the legacy ``/watchlist*`` routes only
# 307-redirect to ``/api/v1/watchlist*`` (which IS gated); the rest is the landing
# redirect and the OpenAPI UI (blocked at Caddy). A new ungated route fails the
# test until it is added here on purpose.
OPEN_REVIEWED: frozenset[str] = frozenset(
    {
        "/",
        "/docs",
        "/docs/oauth2-redirect",
        "/openapi.json",
        "/redoc",
        "/watchlist",
        "/watchlist/{rest:path}",
    }
)


def _enumerate_routes() -> list[tuple[str, str, object]]:
    """(method, path template, route) for every route of the real app."""
    from app.main_api import app

    out: list[tuple[str, str, object]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            pytest.fail(f"route without a path cannot be classified: {route!r}")
        if isinstance(route, APIRoute):
            methods = sorted(route.methods - {"HEAD", "OPTIONS"})
        elif isinstance(route, APIWebSocketRoute):
            methods = ["WEBSOCKET"]
        else:
            # Mounts (/mcp, /app static) and plain Routes (/docs): any method.
            methods = sorted(getattr(route, "methods", None) or {"GET", "POST"})
        out.extend((m, path, route) for m in methods)
    return out


ROUTES = _enumerate_routes()


def _classify(method: str, template: str) -> str:
    gate = classify_path(template)
    if gate == "allowlisted":
        return "public"
    if gate == "open":
        assert template in OPEN_REVIEWED, (
            f"UNCLASSIFIED ungated route {method} {template}: it is outside the "
            "session gate's prefixes, so it is reachable without a login. Review "
            "it and add it to OPEN_REVIEWED here, or move it under /api/."
        )
        return "open"
    customer = [r for r in CUSTOMER_RULES if r.matches(method, template)]
    operator = [r for r in OPERATOR_RULES if r.matches(method, template)]
    assert not (customer and operator), (
        f"AMBIGUOUS route {method} {template}: matches customer rule(s) {customer} "
        f"and operator rule(s) {operator}"
    )
    assert customer or operator, (
        f"UNCLASSIFIED route {method} {template}: add a PolicyRule to CUSTOMER_RULES "
        "(customers may call it) or OPERATOR_RULES (operator-only) in "
        "app/api/auth_middleware.py"
    )
    return "customer" if customer else "operator"


def test_real_app_has_routes_to_check() -> None:
    assert len(ROUTES) > 100, "route enumeration looks broken"


def test_every_route_is_classified() -> None:
    failures: list[str] = []
    for method, template, _ in ROUTES:
        try:
            _classify(method, template)
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n".join(failures)


def test_runtime_policy_agrees_with_explicit_classification() -> None:
    for method, template, _ in ROUTES:
        kind = _classify(method, template)
        if kind in ("customer", "operator"):
            assert policy_tier(method, template) == kind, f"{method} {template}"


def test_no_stale_rules() -> None:
    gated = [(m, t) for m, t, _ in ROUTES if _classify(m, t) in ("customer", "operator")]
    for rule in (*CUSTOMER_RULES, *OPERATOR_RULES):
        assert any(rule.matches(m, t) for m, t in gated), f"rule matches no route: {rule}"


def test_require_operator_routes_are_never_customer_accessible() -> None:
    guarded = 0
    for method, template, route in ROUTES:
        if not isinstance(route, APIRoute):
            continue
        names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
        if names & {"require_operator", "require_operator_principal"}:
            guarded += 1
            assert policy_tier(method, template) == "operator", f"{method} {template}"
    assert guarded >= 10, "expected the jobs/clickhouse/health/ppl/admin routes to carry require_operator"


# ── pure policy examples ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/bars"),
        ("GET", "/api/v1/bars/latest"),
        ("HEAD", "/api/v1/bars"),
        ("POST", "/api/v1/indicators/chart-data"),
        ("GET", "/api/v1/wave/AAPL/history"),
        ("GET", "/api/v1/wave//ES"),
        ("GET", "/api/v1/economic/CPI/history"),
        ("GET", "/api/v1/options/contracts/latest"),
        ("GET", "/api/v1/options/gex/latest"),
        ("GET", "/api/v1/sectors/themes"),
        ("GET", "/api/v1/library/momentum/alerts"),
        ("POST", "/api/v1/backtest/run"),
        ("GET", "/api/v1/backtest/runs/abc"),
        ("GET", "/api/v1/customer/me"),
        ("DELETE", "/api/v1/customer/sessions/abc"),
        ("POST", "/api/v1/my/watchlists/core/members"),
        ("DELETE", "/api/v1/my/watchlists/core/members/AAPL"),
        ("POST", "/cockpit/assistant/conversations"),
        ("POST", "/cockpit/assistant/conversations/abc/turn"),
    ],
)
def test_customer_allowed(method: str, path: str) -> None:
    assert policy_tier(method, path) == "customer"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # the audit findings
        ("POST", "/api/v1/stream"),
        ("DELETE", "/api/v1/stream/AAPL"),
        ("POST", "/api/v1/stream/import"),
        ("POST", "/api/v1/stream/futures"),
        ("POST", "/api/v1/backfill"),
        ("POST", "/api/v1/backfill/deep"),
        ("GET", "/api/v1/journal/trades"),
        ("GET", "/api/v1/journal/accounts"),
        ("POST", "/api/v1/journal/sync"),
        ("POST", "/api/v1/watchlists"),
        ("DELETE", "/api/v1/watchlists/core"),
        ("POST", "/api/v1/watchlist/add"),
        ("POST", "/api/v1/sectors/themes"),
        ("DELETE", "/api/v1/sectors/themes/ai"),
        ("POST", "/api/v1/strategies"),
        ("GET", "/api/v1/strategies/leaderboard"),
        ("POST", "/api/v1/monitors/start"),
        ("GET", "/api/v1/lake/bars"),
        # boundaries: wrong method, sibling prefix, non-latest variant
        ("POST", "/api/v1/bars"),
        ("PUT", "/api/v1/backtest/run"),
        ("GET", "/api/v1/waves"),
        ("GET", "/api/v1/options/contracts"),
        ("GET", "/api/v1/indicators/series"),
        ("GET", "/api/v1/customerx/me"),
        # non-HTTP / tooling / legacy
        ("WEBSOCKET", "/ws/signals"),
        ("GET", "/mcp"),
        ("POST", "/mcp/messages"),
        ("GET", "/api/anything"),
        # a route nobody has classified yet
        ("GET", "/api/v1/brand_new_thing"),
    ],
)
def test_operator_only(method: str, path: str) -> None:
    assert policy_tier(method, path) == "operator"


# ── behaviour on the real app ─────────────────────────────────────────


def _principal(*, operator: bool) -> Principal:
    return Principal(
        user_id=uuid4(),
        tenant_id=uuid4(),
        session_id=uuid4(),
        roles=frozenset({Role.OWNER}),
        permissions=frozenset({"operator.access"}) if operator else frozenset(),
    )


class _FakeIdentity:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    def authenticate_session(self, token: str) -> Principal | None:
        return self.principal if token == SESSION_TOKEN else None

    def get_current_user(self, principal: Principal):  # noqa: ANN201 — contract used by /customer/me
        from app.services.identity.schemas import CurrentUserResponse

        return CurrentUserResponse(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            email="friend@example.com",
            display_name="Friend",
            roles=principal.roles,
            permissions=principal.permissions,
            entitlements=principal.entitlements,
        )


@pytest.fixture
def signed_in_client(monkeypatch: pytest.MonkeyPatch):
    """Factory: real app, auth on, a faked session for the given principal.

    The client is NOT used as a context manager, so no lifespan (and no
    infrastructure) starts. Operator-tier requests are refused by the gate
    before any handler runs, so nothing below the middleware is exercised.
    """
    from app.main_api import app

    def make(*, operator: bool) -> TestClient:
        fake = _FakeIdentity(_principal(operator=operator))
        monkeypatch.setattr(settings, "auth_enabled", True)
        monkeypatch.setattr(settings, "identity_database_url", "postgresql+psycopg://t:t@localhost/unused")
        monkeypatch.setattr(auth_dependencies, "get_identity_service", lambda: fake)
        app.dependency_overrides[REAL_GET_IDENTITY_SERVICE] = lambda: fake
        client = TestClient(app)
        client.cookies.set(settings.auth_cookie_name, SESSION_TOKEN)
        return client

    yield make
    app.dependency_overrides.clear()


def _concrete(template: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", template)


def test_non_operator_is_refused_on_every_operator_route(signed_in_client) -> None:
    client = signed_in_client(operator=False)
    checked = 0
    for method, template, _ in ROUTES:
        if _classify(method, template) != "operator":
            continue
        checked += 1
        path = _concrete(template)
        if method == "WEBSOCKET":
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect(path):
                    pass  # pragma: no cover — the handshake must be refused
            assert excinfo.value.code == 1008, path
            assert excinfo.value.reason == "forbidden", path
            continue
        response = client.request(method, path, follow_redirects=False)
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
        body = response.json()
        assert body["error"] == "forbidden" and body["code"] == "forbidden", f"{method} {path}"
    assert checked >= 60


def test_anonymous_still_gets_401_not_403(signed_in_client) -> None:
    client = signed_in_client(operator=False)
    client.cookies.clear()
    response = client.post("/api/v1/stream", json={})
    assert response.status_code == 401
    assert response.json()["error"] == "authentication_required"


def test_non_operator_reaches_a_customer_route(signed_in_client) -> None:
    """A customer rule is not blocked by the gate: /customer/me is Principal-scoped
    and answers from the fake identity service, no infrastructure involved."""
    client = signed_in_client(operator=False)
    response = client.get("/api/v1/customer/me")
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "friend@example.com"


def test_disabled_auth_leaves_operator_routes_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTH_ENABLED=false (local single-operator dev): the policy never applies."""
    from app.main_api import app

    monkeypatch.setattr(settings, "auth_enabled", False)

    def _must_not_resolve():
        raise AssertionError("gate resolved a session with auth disabled")

    monkeypatch.setattr(auth_dependencies, "get_identity_service", _must_not_resolve)
    client = TestClient(app)
    # /openapi.json is ungated; a gated operator route must simply not be refused
    # by the policy. /api/v1/jobs is operator-only and a no-op gate when auth is off.
    response = client.get("/api/v1/jobs")
    assert response.status_code not in (401, 403)
