"""Real PostgreSQL contract tests for the customer identity repository.

Set TEST_IDENTITY_DATABASE_URL to a disposable database whose name ends in
``_test``. The suite upgrades from an empty schema and downgrades on teardown.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import settings
from app.db.postgres import create_identity_engine, create_identity_session_factory
from app.services.identity.repository import PostgresIdentityRepository
from app.services.identity.schemas import (
    CreateLoginTransactionCommand,
    CreateSessionCommand,
    CreateSecurityEventCommand,
    ExternalIdentityClaim,
    ProvisionAccountCommand,
    SecurityEventType,
)
from app.services.identity.security import hash_session_token


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def identity_repository() -> Iterator[PostgresIdentityRepository]:
    database_url = os.getenv("TEST_IDENTITY_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_IDENTITY_DATABASE_URL is not configured")
    database_name = make_url(database_url).database or ""
    if not database_name.endswith("_test"):
        pytest.fail("TEST_IDENTITY_DATABASE_URL database name must end with '_test'")

    prior_url = settings.identity_database_url
    settings.identity_database_url = database_url
    alembic_config = Config("alembic.ini")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    engine = create_identity_engine(database_url)
    repository = PostgresIdentityRepository(
        session_factory=create_identity_session_factory(engine)
    )
    try:
        yield repository
    finally:
        engine.dispose()
        command.downgrade(alembic_config, "base")
        settings.identity_database_url = prior_url


def _claim(*, subject: str, email: str = "trader@example.com") -> ExternalIdentityClaim:
    return ExternalIdentityClaim(
        provider="cognito",
        subject=subject,
        email=email,
        email_verified=True,
        display_name="Trader",
    )


def test_provision_is_idempotent_and_email_collision_requires_linking(
    identity_repository: PostgresIdentityRepository,
) -> None:
    first = identity_repository.provision_personal_account(
        ProvisionAccountCommand(identity=_claim(subject="subject-1"))
    )
    replay = identity_repository.provision_personal_account(
        ProvisionAccountCommand(identity=_claim(subject="subject-1"))
    )
    collision = identity_repository.provision_personal_account(
        ProvisionAccountCommand(identity=_claim(subject="subject-2"))
    )

    assert first.status == "created"
    assert replay.status == "existing"
    assert replay.account == first.account
    assert collision.status == "conflict"
    assert collision.error_code == "identity_link_required"


def test_session_authentication_and_revocation(
    identity_repository: PostgresIdentityRepository,
) -> None:
    provisioned = identity_repository.provision_personal_account(
        ProvisionAccountCommand(identity=_claim(subject="session-subject", email="session@example.com"))
    )
    assert provisioned.account is not None
    now = datetime.now(timezone.utc)
    token_hash = hash_session_token("integration-session-token")
    created = identity_repository.create_session(
        CreateSessionCommand(
            user_id=provisioned.account.user_id,
            tenant_id=provisioned.account.tenant_id,
            token_hash=token_hash,
            csrf_token_hash=hash_session_token("integration-csrf-token"),
            expires_at=now + timedelta(hours=1),
        )
    )
    assert created.status == "created"
    assert created.session is not None

    principal = identity_repository.get_principal_by_token_hash(token_hash, now=now)
    assert principal is not None
    assert principal.user_id == provisioned.account.user_id
    assert principal.tenant_id == provisioned.account.tenant_id

    revoked = identity_repository.revoke_session(created.session.id, now=now)
    assert revoked.status == "revoked"
    assert identity_repository.get_principal_by_token_hash(token_hash, now=now) is None


def test_session_management_is_user_and_tenant_scoped(
    identity_repository: PostgresIdentityRepository,
) -> None:
    first = identity_repository.provision_personal_account(
        ProvisionAccountCommand(
            identity=_claim(subject="managed-1", email="managed-1@example.com")
        )
    )
    second = identity_repository.provision_personal_account(
        ProvisionAccountCommand(
            identity=_claim(subject="managed-2", email="managed-2@example.com")
        )
    )
    assert first.account is not None
    assert second.account is not None
    now = datetime.now(timezone.utc)

    def create(account, suffix: str):
        result = identity_repository.create_session(
            CreateSessionCommand(
                user_id=account.user_id,
                tenant_id=account.tenant_id,
                token_hash=hash_session_token(f"managed-token-{suffix}"),
                csrf_token_hash=hash_session_token(f"managed-csrf-{suffix}"),
                expires_at=now + timedelta(hours=1),
            )
        )
        assert result.session is not None
        return result.session

    current = create(first.account, "current")
    other = create(first.account, "other")
    foreign = create(second.account, "foreign")

    listed = identity_repository.list_active_sessions(
        user_id=first.account.user_id,
        tenant_id=first.account.tenant_id,
        now=now,
    )
    denied_foreign = identity_repository.revoke_user_session(
        user_id=first.account.user_id,
        tenant_id=first.account.tenant_id,
        session_id=foreign.id,
        now=now,
    )
    bulk = identity_repository.revoke_other_sessions(
        user_id=first.account.user_id,
        tenant_id=first.account.tenant_id,
        current_session_id=current.id,
        now=now,
    )

    assert {session.id for session in listed} == {current.id, other.id}
    assert denied_foreign.status == "not_found"
    assert bulk.status == "revoked"
    assert bulk.revoked_count == 1
    remaining = identity_repository.list_active_sessions(
        user_id=first.account.user_id,
        tenant_id=first.account.tenant_id,
        now=now,
    )
    assert [session.id for session in remaining] == [current.id]
    foreign_principal = identity_repository.get_principal_by_token_hash(
        hash_session_token("managed-token-foreign"), now=now
    )
    assert foreign_principal is not None

    recorded = identity_repository.create_security_event(
        CreateSecurityEventCommand(
            user_id=first.account.user_id,
            tenant_id=first.account.tenant_id,
            session_id=current.id,
            event_type=SecurityEventType.LOGIN_SUCCEEDED,
        )
    )
    events = identity_repository.list_security_events(
        user_id=first.account.user_id,
        tenant_id=first.account.tenant_id,
        limit=20,
    )
    assert recorded.status == "created"
    assert [event.event_type for event in events] == [
        SecurityEventType.LOGIN_SUCCEEDED
    ]


def test_login_transaction_is_single_use(
    identity_repository: PostgresIdentityRepository,
) -> None:
    now = datetime.now(timezone.utc)
    state_hash = hash_session_token("one-time-oauth-state")
    created = identity_repository.create_login_transaction(
        CreateLoginTransactionCommand(
            state_hash=state_hash,
            nonce="n" * 43,
            code_verifier="v" * 64,
            return_to="/app/alerts",
            expires_at=now + timedelta(minutes=10),
        )
    )
    assert created.status == "created"

    consumed = identity_repository.consume_login_transaction(state_hash, now=now)
    replayed = identity_repository.consume_login_transaction(state_hash, now=now)
    assert consumed.status == "consumed"
    assert consumed.transaction is not None
    assert consumed.transaction.return_to == "/app/alerts"
    assert replayed.status == "replayed"
