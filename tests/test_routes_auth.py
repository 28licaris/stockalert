"""HTTP contracts for login, callback cookies, logout CSRF, and route guards."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import auth_dependencies
from app.api.auth_dependencies import get_authentication_service
from app.config import settings
from app.main_api import app
from app.services.identity.schemas import (
    BeginLoginResult,
    CompleteLoginResult,
    CurrentUserResponse,
    IssuedSession,
    Principal,
    RevokeSessionResult,
    Role,
    SessionRecord,
)


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


class FakeAuthenticationService:
    revoked: UUID | None = None
    begin_login_kwargs: dict[str, object] | None = None

    async def begin_login(
        self,
        *,
        return_to: str | None,
        identity_provider: str | None = None,
        screen_hint: str | None = None,
        prompt: str | None = None,
    ) -> BeginLoginResult:
        self.begin_login_kwargs = {
            "return_to": return_to,
            "identity_provider": identity_provider,
            "screen_hint": screen_hint,
            "prompt": prompt,
        }
        return BeginLoginResult(
            status="ok", authorization_url="https://cognito.example/authorize"
        )

    async def complete_login(self, *, code: str, state: str) -> CompleteLoginResult:
        return CompleteLoginResult(
            status="ok",
            return_to="/app/alerts",
            issued_session=IssuedSession(
                token="session-token",
                csrf_token="csrf-token",
                session=SessionRecord(
                    id=uuid4(),
                    user_id=uuid4(),
                    tenant_id=uuid4(),
                    created_at=NOW,
                    expires_at=NOW + timedelta(hours=8),
                ),
            ),
        )

    def logout_url(self) -> str:
        return "https://cognito.example/logout"

    def password_reset_url(self) -> str:
        return "https://cognito.example/forgotPassword"

    def revoke_session(self, session_id: UUID) -> RevokeSessionResult:
        self.revoked = session_id
        return RevokeSessionResult(status="revoked")


class FakeIdentityService:
    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal

    def authenticate_session(self, token: str) -> Principal | None:
        return self.principal if token == "session-token" else None

    def validate_csrf(self, session_id: UUID, csrf_token: str) -> bool:
        return csrf_token == "csrf-token"

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None:
        return CurrentUserResponse(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            email="trader@example.com",
            display_name="Test Trader",
            roles=principal.roles,
            permissions=principal.permissions,
            entitlements=principal.entitlements,
        )


def _principal(*, operator: bool = False) -> Principal:
    return Principal(
        user_id=uuid4(),
        tenant_id=uuid4(),
        session_id=uuid4(),
        roles=frozenset({Role.OWNER}),
        permissions=frozenset({"operator.access"}) if operator else frozenset(),
    )


def _configure_auth(monkeypatch, principal: Principal | None) -> FakeAuthenticationService:
    fake_auth = FakeAuthenticationService()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    monkeypatch.setattr(
        settings,
        "identity_database_url",
        "postgresql+psycopg://test:test@localhost/stockalert_identity_test",
    )
    fake_identity = FakeIdentityService(principal)
    app.dependency_overrides[get_authentication_service] = lambda: fake_auth
    app.dependency_overrides[auth_dependencies.get_identity_service] = lambda: fake_identity
    return fake_auth


def test_auth_disabled_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)
    auth_dependencies.clear_auth_dependency_caches()
    client = TestClient(app)
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json()["code"] == "auth_disabled"


def test_enabled_auth_without_database_fails_as_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "identity_database_url", "")
    response = TestClient(app).get("/api/v1/customer/me")
    assert response.status_code == 503
    assert response.json()["code"] == "auth_not_configured"


def test_login_redirects_to_provider(monkeypatch) -> None:
    _configure_auth(monkeypatch, None)
    try:
        response = TestClient(app).get("/auth/login", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "https://cognito.example/authorize"
    finally:
        app.dependency_overrides.clear()


def test_signup_redirects_to_provider_with_signup_hint(monkeypatch) -> None:
    fake_auth = _configure_auth(monkeypatch, None)
    try:
        response = TestClient(app).get(
            "/auth/login",
            params={"mode": "signup"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "https://cognito.example/authorize"
        assert fake_auth.begin_login_kwargs == {
            "return_to": None,
            "identity_provider": None,
            "screen_hint": "signup",
            "prompt": None,
        }
    finally:
        app.dependency_overrides.clear()


def test_password_reset_redirects_to_provider(monkeypatch) -> None:
    _configure_auth(monkeypatch, None)
    try:
        response = TestClient(app).get("/auth/password-reset", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "https://cognito.example/forgotPassword"
    finally:
        app.dependency_overrides.clear()


def test_callback_sets_opaque_and_csrf_cookies(monkeypatch) -> None:
    _configure_auth(monkeypatch, None)
    try:
        response = TestClient(app).get(
            "/auth/callback",
            params={"code": "code", "state": "s" * 43},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/app/alerts"
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookies if c.startswith("stockalert_session="))
        csrf_cookie = next(c for c in cookies if c.startswith("stockalert_csrf="))
        assert "HttpOnly" in session_cookie
        assert "SameSite=lax" in session_cookie
        assert "HttpOnly" not in csrf_cookie
    finally:
        app.dependency_overrides.clear()


def test_customer_and_operator_boundaries(monkeypatch) -> None:
    principal = _principal(operator=False)
    _configure_auth(monkeypatch, principal)
    try:
        client = TestClient(app)
        client.cookies.set("stockalert_session", "session-token")
        customer = client.get("/api/v1/customer/me")
        operator = client.get("/api/v1/admin/me")
        assert customer.status_code == 200
        assert customer.json()["tenant_id"] == str(principal.tenant_id)
        assert customer.json()["display_name"] == "Test Trader"
        assert operator.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_logout_requires_matching_csrf_and_revokes_session(monkeypatch) -> None:
    principal = _principal()
    fake_auth = _configure_auth(monkeypatch, principal)
    try:
        client = TestClient(app)
        client.cookies.set("stockalert_session", "session-token")
        client.cookies.set("stockalert_csrf", "csrf-token")
        denied = client.post("/auth/logout", follow_redirects=False)
        assert denied.status_code == 403

        response = client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": "csrf-token"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert response.json()["redirect_url"] == "https://cognito.example/logout"
        assert fake_auth.revoked == principal.session_id
    finally:
        app.dependency_overrides.clear()
