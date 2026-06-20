"""OAuth orchestration tests over provider/repository fakes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.services.identity.auth_service import OAuthAuthenticationService
from app.services.identity.schemas import (
    AccountRef,
    CognitoTokenSet,
    ConsumeLoginTransactionResult,
    CreateLoginTransactionCommand,
    CreateLoginTransactionResult,
    CreateSessionCommand,
    CreateSessionResult,
    CurrentUserResponse,
    ExternalIdentityClaim,
    LoginTransaction,
    Principal,
    ProvisionAccountCommand,
    ProvisionAccountResult,
    RevokeSessionResult,
    Role,
    SessionRecord,
)
from app.services.identity.security import hash_session_token
from app.services.identity.service import IdentityService


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


class FakeProvider:
    exchanged: tuple[str, str, str] | None = None

    def authorization_url(self, **kwargs: str) -> str:
        return "https://cognito.example/authorize?state=" + kwargs["state"]

    async def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> CognitoTokenSet:
        self.exchanged = (code, code_verifier, expected_nonce)
        return CognitoTokenSet(
            identity=ExternalIdentityClaim(
                provider="cognito",
                subject="subject-1",
                email="trader@example.com",
                email_verified=True,
                display_name="Trader",
            ),
            access_token="access",
            id_token="identity",
            expires_in=3600,
        )

    def logout_url(self, *, logout_uri: str) -> str:
        return "https://cognito.example/logout?to=" + logout_uri


class FakeRepository:
    def __init__(self) -> None:
        self.login_command: CreateLoginTransactionCommand | None = None
        self.consume_result = ConsumeLoginTransactionResult(status="not_found")
        self.session_command: CreateSessionCommand | None = None
        self.user_id = uuid4()
        self.tenant_id = uuid4()

    def create_login_transaction(
        self, command: CreateLoginTransactionCommand
    ) -> CreateLoginTransactionResult:
        self.login_command = command
        return CreateLoginTransactionResult(status="created", transaction_id=uuid4())

    def consume_login_transaction(
        self, state_hash: str, *, now: datetime
    ) -> ConsumeLoginTransactionResult:
        assert state_hash == hash_session_token("state-value")
        assert now == NOW
        return self.consume_result

    def provision_personal_account(
        self, command: ProvisionAccountCommand
    ) -> ProvisionAccountResult:
        return ProvisionAccountResult(
            status="created",
            account=AccountRef(
                user_id=self.user_id, tenant_id=self.tenant_id, role=Role.OWNER
            ),
        )

    def create_session(self, command: CreateSessionCommand) -> CreateSessionResult:
        self.session_command = command
        return CreateSessionResult(
            status="created",
            session=SessionRecord(
                id=uuid4(),
                user_id=command.user_id,
                tenant_id=command.tenant_id,
                created_at=NOW,
                expires_at=command.expires_at,
            ),
        )

    def get_principal_by_token_hash(
        self, token_hash: str, *, now: datetime
    ) -> Principal | None:
        return None

    def revoke_session(self, session_id: UUID, *, now: datetime) -> RevokeSessionResult:
        return RevokeSessionResult(status="revoked")

    def session_matches_csrf(self, session_id: UUID, csrf_token_hash: str) -> bool:
        return True

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None:
        return None


def _service(repo: FakeRepository, provider: FakeProvider) -> OAuthAuthenticationService:
    identity = IdentityService(
        repository=repo,
        clock=lambda: NOW,
        token_factory=lambda: "session-token",
        csrf_token_factory=lambda: "csrf-token",
    )
    return OAuthAuthenticationService(
        provider=provider,
        repository=repo,
        identity_service=identity,
        redirect_uri="http://localhost:8000/auth/callback",
        logout_uri="http://localhost:5173/app/login",
        session_ttl=timedelta(hours=8),
        clock=lambda: NOW,
        state_factory=lambda: "state-value",
        nonce_factory=lambda: "n" * 43,
        verifier_factory=lambda: "v" * 64,
    )


@pytest.mark.asyncio
async def test_begin_login_persists_transaction_and_rejects_open_redirect() -> None:
    repo = FakeRepository()
    result = await _service(repo, FakeProvider()).begin_login(
        return_to="https://evil.example/steal"
    )
    assert result.status == "ok"
    assert repo.login_command is not None
    assert repo.login_command.return_to == "/app/"
    assert repo.login_command.state_hash == hash_session_token("state-value")
    assert "state-value" in (result.authorization_url or "")


@pytest.mark.asyncio
async def test_complete_login_consumes_state_provisions_and_issues_session() -> None:
    repo = FakeRepository()
    repo.consume_result = ConsumeLoginTransactionResult(
        status="consumed",
        transaction=LoginTransaction(
            id=uuid4(),
            nonce="n" * 43,
            code_verifier="v" * 64,
            return_to="/app/alerts",
            expires_at=NOW + timedelta(minutes=10),
        ),
    )
    provider = FakeProvider()
    result = await _service(repo, provider).complete_login(
        code="authorization-code", state="state-value"
    )
    assert result.status == "ok"
    assert result.return_to == "/app/alerts"
    assert result.issued_session is not None
    assert result.issued_session.token.get_secret_value() == "session-token"
    assert provider.exchanged == ("authorization-code", "v" * 64, "n" * 43)


@pytest.mark.asyncio
async def test_complete_login_rejects_replayed_state_before_provider_call() -> None:
    repo = FakeRepository()
    repo.consume_result = ConsumeLoginTransactionResult(status="replayed")
    provider = FakeProvider()
    result = await _service(repo, provider).complete_login(
        code="authorization-code", state="state-value"
    )
    assert result.status == "replayed"
    assert provider.exchanged is None
