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
    ExternalIdentityClaim,
    ProvisionAccountCommand,
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
