"""Unit tests for the identity service's public Pydantic contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.identity.schemas import (
    AccountRef,
    CreateSessionCommand,
    ExternalIdentityClaim,
    IssuedSession,
    ProvisionAccountResult,
    Role,
    SessionRecord,
)
from app.services.identity.security import (
    generate_session_token,
    hash_session_token,
    safe_return_path,
)


def test_external_identity_normalizes_provider_and_email() -> None:
    claim = ExternalIdentityClaim(
        provider=" COGNITO ",
        subject="subject-1",
        email=" User@Example.COM ",
        email_verified=True,
        display_name="Trader",
    )
    assert claim.provider == "cognito"
    assert claim.email == "user@example.com"


@pytest.mark.parametrize("email", ["missing-at", "@example.com", "user@localhost"])
def test_external_identity_rejects_invalid_email(email: str) -> None:
    with pytest.raises(ValidationError):
        ExternalIdentityClaim(
            provider="cognito",
            subject="subject-1",
            email=email,
            email_verified=True,
            display_name="Trader",
        )


def test_provision_result_enforces_success_shape() -> None:
    with pytest.raises(ValidationError):
        ProvisionAccountResult(status="created")

    account = AccountRef(user_id=uuid4(), tenant_id=uuid4(), role=Role.OWNER)
    result = ProvisionAccountResult(status="existing", account=account)
    assert result.account == account


def test_session_command_requires_sha256_hex_digest() -> None:
    with pytest.raises(ValidationError):
        CreateSessionCommand(
            user_id=uuid4(),
            tenant_id=uuid4(),
            token_hash="raw-session-token",
            csrf_token_hash="b" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )


def test_session_command_requires_timezone_aware_expiry() -> None:
    with pytest.raises(ValidationError):
        CreateSessionCommand(
            user_id=uuid4(),
            tenant_id=uuid4(),
            token_hash="a" * 64,
            csrf_token_hash="b" * 64,
            expires_at=datetime(2026, 6, 19, 12, 0),
        )


def test_session_token_is_random_hashed_and_redacted() -> None:
    first = generate_session_token()
    second = generate_session_token()
    assert first != second
    digest = hash_session_token(first)
    assert len(digest) == 64
    assert first not in digest

    now = datetime.now(timezone.utc)
    issued = IssuedSession(
        token=first,
        csrf_token="csrf-secret",
        session=SessionRecord(
            id=uuid4(),
            user_id=uuid4(),
            tenant_id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
    )
    assert first not in repr(issued)
    assert first not in issued.model_dump_json()
    assert "csrf-secret" not in issued.model_dump_json()


@pytest.mark.parametrize(
    "unsafe",
    ["https://evil.example", "//evil.example", "/\\evil.example", "/ok\r\nX: bad"],
)
def test_return_path_rejects_cross_origin_and_header_tricks(unsafe: str) -> None:
    assert safe_return_path(unsafe) == "/app/"


def test_return_path_preserves_safe_relative_destination() -> None:
    assert safe_return_path("/app/alerts?symbol=AAPL") == "/app/alerts?symbol=AAPL"
