"""TOTP MFA orchestration and provider-session encryption tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.identity.mfa_service import MfaService, MfaServiceError
from app.services.identity.provider_session import (
    LocalAesGcmProviderSessionCipher,
    ProviderSessionMaterial,
)
from app.services.identity.schemas import (
    CreateSecurityEventResult,
    CurrentUserResponse,
    Principal,
    Role,
    SecurityEventRecord,
    SecurityEventType,
)


NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self, ciphertext: bytes | None) -> None:
        self.ciphertext = ciphertext

    def get_provider_session_ciphertext(self, principal: Principal) -> bytes | None:
        return self.ciphertext

    def get_current_user(self, principal: Principal) -> CurrentUserResponse:
        return CurrentUserResponse(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            email="trader@example.com",
            display_name="Trader",
            roles=principal.roles,
            permissions=frozenset(),
            entitlements=frozenset(),
        )


class FakeProvider:
    enabled = False
    verified_code: str | None = None

    def get_mfa_status(self, *, access_token: str) -> tuple[bool, bool]:
        assert access_token == "access-token"
        return self.enabled, self.enabled

    def associate_software_token(self, *, access_token: str) -> str:
        assert access_token == "access-token"
        return "ABCDEFGHIJKLMNOP"

    def verify_software_token(self, *, access_token: str, code: str) -> bool:
        self.verified_code = code
        return code == "123456"


class FakeIdentityService:
    def record_security_event(self, principal, event_type, *, session_id=None):
        command = {
            "user_id": principal.user_id,
            "tenant_id": principal.tenant_id,
            "session_id": principal.session_id,
            "event_type": event_type,
        }
        return CreateSecurityEventResult(
            status="created",
            event=SecurityEventRecord(id=uuid4(), created_at=NOW, **command),
        )


def _principal() -> Principal:
    return Principal(
        user_id=uuid4(),
        tenant_id=uuid4(),
        session_id=uuid4(),
        roles=frozenset({Role.OWNER}),
    )


def _service(source_provider: str = "cognito", *, expired: bool = False) -> MfaService:
    cipher = LocalAesGcmProviderSessionCipher("test-client-secret")
    material = ProviderSessionMaterial(
        access_token="access-token",
        expires_at=NOW + (-timedelta(seconds=1) if expired else timedelta(hours=1)),
        source_provider=source_provider,
    )
    return MfaService(
        repository=FakeRepository(cipher.encrypt(material)),
        provider=FakeProvider(),
        cipher=cipher,
        identity_service=FakeIdentityService(),
        clock=lambda: NOW,
    )


def test_local_provider_cipher_round_trips_and_rejects_tampering() -> None:
    cipher = LocalAesGcmProviderSessionCipher("test-client-secret")
    material = ProviderSessionMaterial(
        access_token="sensitive-token",
        expires_at=NOW + timedelta(hours=1),
        source_provider="cognito",
    )
    encrypted = cipher.encrypt(material)
    assert b"sensitive-token" not in encrypted
    assert cipher.decrypt(encrypted) == material
    with pytest.raises(Exception):
        cipher.decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))


def test_mfa_status_and_enrollment_use_recent_cognito_session() -> None:
    service = _service()
    principal = _principal()
    assert service.status(principal).supported is True
    enrollment = service.begin_enrollment(principal)
    assert enrollment.secret_code == "ABCDEFGHIJKLMNOP"
    assert enrollment.otpauth_uri.startswith("otpauth://totp/StockAlert%3A")
    assert service.verify_enrollment(principal, "123456").enabled is True


def test_mfa_requires_recent_auth_and_rejects_federated_sessions() -> None:
    principal = _principal()
    assert _service(expired=True).status(principal).reauthentication_required is True
    with pytest.raises(MfaServiceError, match="Sign in again"):
        _service(expired=True).begin_enrollment(principal)
    assert _service("google").status(principal).supported is False
