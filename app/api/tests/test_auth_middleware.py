"""Contract tests for the deny-by-default session gate (deployment_dev.md §5, C1).

Two layers:

* ``SessionGateMiddleware`` on a throwaway FastAPI app with both collaborators
  injected (the enabled flag and the principal resolver). Pins the path
  policy, the 401 body, websocket denial, the 503 fail-closed branch and the
  "disabled = pass-through, resolver never called" invariant.
* The real ``app.main_api.app`` with a fake identity service injected through
  the one seam both the gate and ``require_operator`` read
  (``auth_dependencies.get_identity_service``). Pins the spec's behaviour
  matrix: 401 without a cookie, allowlisted paths pass, a customer session
  passes the gate but still 403s on operator routes, auth-off is unchanged.

No Cognito, no PostgreSQL, no ClickHouse: every collaborator is injected.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, WebSocket, status
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect

from app.api import auth_dependencies
from app.api.auth_middleware import (
    AUTH_REQUIRED_CODE,
    WEBSOCKET_POLICY_VIOLATION,
    SessionGateMiddleware,
    classify_path,
)
from app.config import settings
from app.services.identity.schemas import Principal, Role

SESSION_TOKEN = "session-token"

# Captured before any monkeypatch: routes bind ``Depends(get_identity_service)``
# to this object at import, so ``dependency_overrides`` must be keyed by it.
REAL_GET_IDENTITY_SERVICE = auth_dependencies.get_identity_service


def _principal(*, operator: bool = False) -> Principal:
    return Principal(
        user_id=uuid4(),
        tenant_id=uuid4(),
        session_id=uuid4(),
        roles=frozenset({Role.OWNER}),
        permissions=frozenset({"operator.access"}) if operator else frozenset(),
    )


# ─────────────────────────────────────────────────────────────────────
# Path policy (pure)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/auth/login",
        "/auth/callback",
        "/auth/logout",
        "/app",
        "/app/",
        "/app/charts",
        "/app/assets/index-abc123.js",
        "/api/v1/customer/billing/webhook",
    ],
)
def test_classify_allowlisted(path: str) -> None:
    assert classify_path(path) == "allowlisted"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/bars",
        "/api/v1/jobs",
        "/api/v1/customer/me",
        "/api/v1/customer/billing/checkout",  # only the webhook is exempt
        "/api/health/services",  # the composite probe stays gated
        "/api/health/freshness",
        "/api/legacy-path",  # legacy redirect target is gated too
        "/cockpit/assistant/chat",
        "/ws/signals",
        "/mcp",
        "/mcp/",
        "/mcp/messages",
    ],
)
def test_classify_gated(path: str) -> None:
    assert classify_path(path) == "gated"


@pytest.mark.parametrize("path", ["/", "/docs", "/openapi.json", "/health/services", "/watchlist"])
def test_classify_open(path: str) -> None:
    """Not this gate's business: no session check, no allowlist either.

    ``/health/services`` here is a non-route (the real one is ``/api/health/services``)
    and documents that ``/health`` is an exact match, not a prefix.
    """
    assert classify_path(path) == "open"


# ─────────────────────────────────────────────────────────────────────
# Middleware in isolation — injected collaborators
# ─────────────────────────────────────────────────────────────────────


class RecordingResolver:
    """Stands in for the cookie → session lookup; records every call."""

    def __init__(self, principal: Principal | None, *, raises: HTTPException | None = None) -> None:
        self.principal = principal
        self.raises = raises
        self.calls: list[tuple[str, str]] = []

    def __call__(self, connection: HTTPConnection) -> Principal | None:
        self.calls.append((connection.scope["path"], connection.cookies.get("stockalert_session", "")))
        if self.raises is not None:
            raise self.raises
        return self.principal if connection.cookies.get("stockalert_session") == SESSION_TOKEN else None


def _gated_app(*, enabled: bool, resolver: Callable[[HTTPConnection], Principal | None]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionGateMiddleware, is_enabled=lambda: enabled, resolve_principal=resolver)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/login")
    async def login() -> dict[str, str]:
        return {"route": "login"}

    @app.get("/app")
    @app.get("/app/{rest:path}")
    async def spa(rest: str = "") -> dict[str, str]:
        return {"route": "spa"}

    @app.post("/api/v1/customer/billing/webhook")
    async def webhook() -> dict[str, str]:
        return {"route": "webhook"}

    @app.get("/api/v1/bars")
    async def bars() -> dict[str, str]:
        return {"route": "bars"}

    @app.get("/cockpit/assistant/chat")
    async def assistant() -> dict[str, str]:
        return {"route": "assistant"}

    @app.get("/mcp")
    @app.get("/mcp/{rest:path}")
    async def mcp(rest: str = "") -> dict[str, str]:
        return {"route": "mcp"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"route": "root"}

    @app.websocket("/ws/signals")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"route": "ws"})
        await websocket.close()

    return app


GATED_HTTP = ["/api/v1/bars", "/cockpit/assistant/chat", "/mcp", "/mcp/messages"]
ALLOWLISTED_HTTP = [("GET", "/health"), ("GET", "/auth/login"), ("GET", "/app"), ("GET", "/app/charts"), ("POST", "/api/v1/customer/billing/webhook")]


@pytest.mark.parametrize("path", GATED_HTTP)
def test_enabled_gated_path_without_cookie_is_401_with_spec_body(path: str) -> None:
    resolver = RecordingResolver(_principal())
    client = TestClient(_gated_app(enabled=True, resolver=resolver))

    response = client.get(path)

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "authentication_required"  # spec §5 contract
    assert body["code"] == AUTH_REQUIRED_CODE  # cockpit ErrorResponse envelope
    assert body["message"] == "Authentication required."
    assert response.headers["WWW-Authenticate"] == "Session"
    assert response.headers["X-Error-Code"] == AUTH_REQUIRED_CODE
    assert resolver.calls == [(path, "")]


def test_enabled_invalid_cookie_is_401() -> None:
    resolver = RecordingResolver(_principal())
    client = TestClient(_gated_app(enabled=True, resolver=resolver))
    client.cookies.set("stockalert_session", "forged")

    response = client.get("/api/v1/bars")

    assert response.status_code == 401
    assert response.json()["error"] == "authentication_required"
    assert resolver.calls == [("/api/v1/bars", "forged")]


@pytest.mark.parametrize(("method", "path"), ALLOWLISTED_HTTP)
def test_enabled_allowlisted_path_passes_without_touching_sessions(method: str, path: str) -> None:
    resolver = RecordingResolver(None)
    client = TestClient(_gated_app(enabled=True, resolver=resolver))

    response = client.request(method, path)

    assert response.status_code == 200, response.text
    assert resolver.calls == [], "allowlisted paths must not hit the identity store"


def test_enabled_open_path_is_not_this_gates_business() -> None:
    resolver = RecordingResolver(None)
    client = TestClient(_gated_app(enabled=True, resolver=resolver))

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"route": "root"}
    assert resolver.calls == []


@pytest.mark.parametrize("path", GATED_HTTP)
def test_enabled_valid_session_passes(path: str) -> None:
    resolver = RecordingResolver(_principal())
    client = TestClient(_gated_app(enabled=True, resolver=resolver))
    client.cookies.set("stockalert_session", SESSION_TOKEN)

    response = client.get(path)

    assert response.status_code == 200, response.text
    assert resolver.calls == [(path, SESSION_TOKEN)]


def test_enabled_websocket_without_cookie_is_closed_before_accept() -> None:
    resolver = RecordingResolver(_principal())
    client = TestClient(_gated_app(enabled=True, resolver=resolver))

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/signals"):
            pass  # pragma: no cover — the handshake must be refused

    assert excinfo.value.code == WEBSOCKET_POLICY_VIOLATION
    assert excinfo.value.reason == AUTH_REQUIRED_CODE
    assert resolver.calls == [("/ws/signals", "")]


def test_enabled_websocket_with_valid_session_connects() -> None:
    resolver = RecordingResolver(_principal())
    client = TestClient(_gated_app(enabled=True, resolver=resolver))
    client.cookies.set("stockalert_session", SESSION_TOKEN)

    with client.websocket_connect("/ws/signals") as ws:
        assert ws.receive_json() == {"route": "ws"}

    assert resolver.calls == [("/ws/signals", SESSION_TOKEN)]


def test_enabled_but_unconfigured_identity_fails_closed_with_503_envelope() -> None:
    """The resolver 503s when auth is on but the identity DB is absent (same
    exception ``get_identity_service`` raises). Must not become a 500 and must
    not let the request through."""
    unconfigured = HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Customer authentication is not configured.",
        headers={"X-Error-Code": "auth_not_configured"},
    )
    resolver = RecordingResolver(None, raises=unconfigured)
    client = TestClient(_gated_app(enabled=True, resolver=resolver))

    response = client.get("/api/v1/bars")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "auth_not_configured"
    assert body["error"] == "auth_not_configured"
    assert body["message"] == "Customer authentication is not configured."
    assert response.headers["X-Error-Code"] == "auth_not_configured"


@pytest.mark.parametrize("path", GATED_HTTP + [p for _, p in ALLOWLISTED_HTTP if p != "/api/v1/customer/billing/webhook"])
def test_disabled_is_a_pure_pass_through(path: str) -> None:
    resolver = RecordingResolver(None)
    client = TestClient(_gated_app(enabled=False, resolver=resolver))

    response = client.get(path)

    assert response.status_code == 200, response.text
    assert resolver.calls == [], "with auth disabled the gate must never resolve a session"


def test_disabled_websocket_connects_without_cookie() -> None:
    resolver = RecordingResolver(None)
    client = TestClient(_gated_app(enabled=False, resolver=resolver))

    with client.websocket_connect("/ws/signals") as ws:
        assert ws.receive_json() == {"route": "ws"}

    assert resolver.calls == []


# ─────────────────────────────────────────────────────────────────────
# Real app — spec §5 behaviour matrix
# ─────────────────────────────────────────────────────────────────────


class FakeIdentityService:
    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal

    def authenticate_session(self, token: str) -> Principal | None:
        return self.principal if token == SESSION_TOKEN else None

    def get_current_user(self, principal: Principal):  # noqa: ANN201 — mirrors the contract used by /customer/me
        from app.services.identity.schemas import CurrentUserResponse

        return CurrentUserResponse(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            email="trader@example.com",
            display_name="Test Trader",
            roles=principal.roles,
            permissions=principal.permissions,
            entitlements=principal.entitlements,
        )


class FakeAuthenticationService:
    async def begin_login(self, **_: object):  # noqa: ANN201
        from app.services.identity.schemas import BeginLoginResult

        return BeginLoginResult(status="ok", authorization_url="https://cognito.example/authorize")


class FakeBillingService:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    def handle_webhook(self, payload: bytes, sig_header: str) -> None:
        self.calls.append((payload, sig_header))


@pytest.fixture
def real_app(monkeypatch: pytest.MonkeyPatch):
    """``app.main_api.app`` with a clean JobRegistry, a stubbed ClickHouse ping
    and the lifespan disabled, so no test here reaches infrastructure."""
    from contextlib import asynccontextmanager

    from app import main_api
    from app.api import routes_jobs as routes_jobs_module
    from app.services.jobs import service as jobs_module
    from app.services.jobs.service import JobRegistry

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    monkeypatch.setattr(main_api.app.router, "lifespan_context", noop_lifespan)
    monkeypatch.setattr(main_api, "ping", lambda: True)
    fresh = JobRegistry()
    monkeypatch.setattr(jobs_module, "job_registry", fresh)
    monkeypatch.setattr(routes_jobs_module, "job_registry", fresh)
    monkeypatch.setattr(jobs_module, "fetch_last_runs", lambda: {})
    yield main_api.app
    main_api.app.dependency_overrides.clear()


def _enable_auth(monkeypatch: pytest.MonkeyPatch, app: FastAPI, principal: Principal | None) -> FakeBillingService:
    """Auth on, identity store faked through the single seam both the gate and
    ``require_operator`` read (the module attribute), plus the Depends overrides
    for routes that inject services."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    monkeypatch.setattr(settings, "identity_database_url", "postgresql+psycopg://test:test@localhost/unused")
    fake_identity = FakeIdentityService(principal)
    fake_billing = FakeBillingService()
    monkeypatch.setattr(auth_dependencies, "get_identity_service", lambda: fake_identity)
    app.dependency_overrides[REAL_GET_IDENTITY_SERVICE] = lambda: fake_identity
    app.dependency_overrides[auth_dependencies.get_authentication_service] = lambda: FakeAuthenticationService()
    app.dependency_overrides[auth_dependencies.get_billing_service] = lambda: fake_billing
    return fake_billing


@pytest.mark.parametrize("path", ["/api/v1/jobs", "/api/v1/bars?symbol=SPY", "/api/v1/customer/me", "/api/health/services", "/mcp"])
def test_real_app_enabled_no_cookie_is_401(real_app: FastAPI, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    """Spec §10 test 2: ``/api/v1/bars?symbol=SPY`` without a cookie → 401."""
    _enable_auth(monkeypatch, real_app, _principal())
    client = TestClient(real_app)

    response = client.get(path)

    assert response.status_code == 401, response.text
    assert response.json()["error"] == "authentication_required"


def test_real_app_enabled_websocket_no_cookie_is_refused(real_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_auth(monkeypatch, real_app, _principal())
    client = TestClient(real_app)

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/signals"):
            pass  # pragma: no cover

    assert excinfo.value.code == WEBSOCKET_POLICY_VIOLATION


def test_real_app_enabled_allowlist_passes(real_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_billing = _enable_auth(monkeypatch, real_app, None)
    client = TestClient(real_app)

    health = client.get("/health")
    login = client.get("/auth/login", follow_redirects=False)
    webhook = client.post(
        "/api/v1/customer/billing/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=abc"}
    )

    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert login.status_code == 302 and login.headers["location"].startswith("https://cognito.example/")
    assert webhook.status_code == 204, webhook.text
    assert fake_billing.calls == [(b"{}", "t=1,v1=abc")]


def test_real_app_enabled_spa_shell_passes_when_built(real_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """``/app`` and ``/app/*`` are allowlisted. The SPA routes only exist when a
    frontend build is present, so assert the gate's verdict rather than the
    route's: never 401, and 200 when the build is on disk."""
    from app import main_api

    _enable_auth(monkeypatch, real_app, None)
    client = TestClient(real_app)

    for path in ("/app", "/app/charts"):
        response = client.get(path)
        assert response.status_code != 401, path
        if main_api._COCKPIT_AVAILABLE:
            assert response.status_code == 200, path
        else:
            assert response.status_code == 404, path


def test_real_app_customer_session_passes_gate_but_operator_route_still_403s(
    real_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §10 test 3: a signed-in customer gets ``403`` on ``GET /api/v1/jobs``
    — from ``require_operator``, which the gate leaves in place."""
    principal = _principal(operator=False)
    _enable_auth(monkeypatch, real_app, principal)
    client = TestClient(real_app)
    client.cookies.set("stockalert_session", SESSION_TOKEN)

    me = client.get("/api/v1/customer/me")
    jobs = client.get("/api/v1/jobs")

    assert me.status_code == 200, me.text
    assert me.json()["tenant_id"] == str(principal.tenant_id)
    assert jobs.status_code == 403, jobs.text
    assert jobs.json()["code"] == "forbidden"


def test_real_app_operator_session_passes_gate_and_operator_route(
    real_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_auth(monkeypatch, real_app, _principal(operator=True))
    client = TestClient(real_app)
    client.cookies.set("stockalert_session", SESSION_TOKEN)

    jobs = client.get("/api/v1/jobs")

    assert jobs.status_code == 200, jobs.text
    assert jobs.json() == {"jobs": []}


def test_real_app_valid_session_websocket_connects(real_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_auth(monkeypatch, real_app, _principal())
    client = TestClient(real_app)
    client.cookies.set("stockalert_session", SESSION_TOKEN)

    with client.websocket_connect("/ws/signals") as ws:
        ws.send_text("ping")  # the endpoint accepts and idles on receive_text


def test_real_app_gate_honours_dependency_overrides_like_the_routes(
    real_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing auth tests inject the identity service only through
    ``app.dependency_overrides``; the gate must resolve it through the same
    seam, or every such test would reach for PostgreSQL."""
    principal = _principal()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "identity_database_url", "postgresql+psycopg://test:test@localhost/unused")
    real_app.dependency_overrides[REAL_GET_IDENTITY_SERVICE] = lambda: FakeIdentityService(principal)
    client = TestClient(real_app)

    anonymous = client.get("/api/v1/customer/me")
    client.cookies.set("stockalert_session", SESSION_TOKEN)
    signed_in = client.get("/api/v1/customer/me")

    assert anonymous.status_code == 401
    assert anonymous.json()["error"] == "authentication_required"
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["tenant_id"] == str(principal.tenant_id)


def test_real_app_enabled_unconfigured_identity_db_is_503_not_500(
    real_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeps the pre-existing contract of ``/api/v1/customer/me`` (503
    ``auth_not_configured``) now that the gate answers first."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "identity_database_url", "")
    auth_dependencies.clear_auth_dependency_caches()
    client = TestClient(real_app)

    response = client.get("/api/v1/customer/me")

    assert response.status_code == 503
    assert response.json()["code"] == "auth_not_configured"


def test_real_app_disabled_is_unchanged(real_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """``AUTH_ENABLED=false``: operator routes stay open to the local operator
    and nothing asks for a cookie."""
    monkeypatch.setattr(settings, "auth_enabled", False)

    def _must_not_resolve() -> FakeIdentityService:
        raise AssertionError("gate resolved a session with auth disabled")

    monkeypatch.setattr(auth_dependencies, "get_identity_service", _must_not_resolve)
    client = TestClient(real_app)

    jobs = client.get("/api/v1/jobs")
    health = client.get("/health")

    assert jobs.status_code == 200, jobs.text
    assert health.status_code == 200
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_text("ping")
