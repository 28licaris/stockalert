"""Provider-independent identity orchestration tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.services.identity.contract import IdentityRepository, IdentityServiceProtocol
from app.services.identity.schemas import (
    AccountRef,
    CreateSessionCommand,
    CreateSessionResult,
    CurrentUserResponse,
    ConsumeLoginTransactionResult,
    CreateLoginTransactionCommand,
    CreateLoginTransactionResult,
    ExternalIdentityClaim,
    IssuedSession,
    Principal,
    ProvisionAccountCommand,
    ProvisionAccountResult,
    RevokeSessionResult,
    RevokeSessionsResult,
    Role,
    SessionRecord,
)
from app.services.identity.security import hash_session_token
from app.services.identity.service import IdentityService


NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


class FakeIdentityRepository:
    def __init__(self) -> None:
        self.created_command: CreateSessionCommand | None = None
        self.lookup_hash: str | None = None
        self.principal: Principal | None = None
        self.csrf_hash: str | None = None
        self.sessions: tuple[SessionRecord, ...] = ()
        self.revoked_user_session: UUID | None = None
        self.revoked_other_current: UUID | None = None

    def provision_personal_account(
        self, command: ProvisionAccountCommand
    ) -> ProvisionAccountResult:
        return ProvisionAccountResult(
            status="created",
            account=AccountRef(user_id=uuid4(), tenant_id=uuid4(), role=Role.OWNER),
        )

    def create_session(self, command: CreateSessionCommand) -> CreateSessionResult:
        self.created_command = command
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
        assert now == NOW
        self.lookup_hash = token_hash
        return self.principal

    def revoke_session(self, session_id: UUID, *, now: datetime) -> RevokeSessionResult:
        assert now == NOW
        return RevokeSessionResult(status="revoked")

    def list_active_sessions(
        self, *, user_id: UUID, tenant_id: UUID, now: datetime
    ) -> tuple[SessionRecord, ...]:
        assert now == NOW
        return self.sessions

    def revoke_user_session(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> RevokeSessionResult:
        assert now == NOW
        self.revoked_user_session = session_id
        return RevokeSessionResult(status="revoked")

    def revoke_other_sessions(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        current_session_id: UUID,
        now: datetime,
    ) -> RevokeSessionsResult:
        assert now == NOW
        self.revoked_other_current = current_session_id
        return RevokeSessionsResult(status="revoked", revoked_count=2)

    def create_login_transaction(
        self, command: CreateLoginTransactionCommand
    ) -> CreateLoginTransactionResult:
        return CreateLoginTransactionResult(status="created", transaction_id=uuid4())

    def consume_login_transaction(
        self, state_hash: str, *, now: datetime
    ) -> ConsumeLoginTransactionResult:
        return ConsumeLoginTransactionResult(status="not_found")

    def session_matches_csrf(self, session_id: UUID, csrf_token_hash: str) -> bool:
        self.csrf_hash = csrf_token_hash
        return True

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None:
        return CurrentUserResponse(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            email="trader@example.com",
            display_name="Trader",
            roles=principal.roles,
            permissions=principal.permissions,
            entitlements=principal.entitlements,
        )


def test_fake_and_service_satisfy_public_protocols() -> None:
    repo = FakeIdentityRepository()
    service = IdentityService(repository=repo)
    assert isinstance(repo, IdentityRepository)
    assert isinstance(service, IdentityServiceProtocol)


def test_issue_session_stores_hash_and_returns_raw_token_once() -> None:
    repo = FakeIdentityRepository()
    service = IdentityService(
        repository=repo,
        clock=lambda: NOW,
        token_factory=lambda: "fixed-secret-token",
        csrf_token_factory=lambda: "fixed-csrf-token",
    )
    user_id = uuid4()
    tenant_id = uuid4()
    expires_at = NOW + timedelta(hours=8)

    result = service.issue_session(
        user_id=user_id, tenant_id=tenant_id, expires_at=expires_at
    )

    assert isinstance(result, IssuedSession)
    assert result.token.get_secret_value() == "fixed-secret-token"
    assert repo.created_command is not None
    assert repo.created_command.token_hash == hash_session_token("fixed-secret-token")
    assert repo.created_command.csrf_token_hash == hash_session_token("fixed-csrf-token")
    assert repo.created_command.user_id == user_id
    assert repo.created_command.tenant_id == tenant_id


def test_authenticate_session_hashes_cookie_before_repository_lookup() -> None:
    repo = FakeIdentityRepository()
    service = IdentityService(repository=repo, clock=lambda: NOW)

    assert service.authenticate_session("browser-cookie") is None
    assert repo.lookup_hash == hash_session_token("browser-cookie")
    assert service.authenticate_session("") is None


def test_unverified_identity_is_denied_before_repository_write() -> None:
    repo = FakeIdentityRepository()
    service = IdentityService(repository=repo, clock=lambda: NOW)
    result = service.provision_personal_account(
        ProvisionAccountCommand(
            identity=ExternalIdentityClaim(
                provider="cognito",
                subject="subject-1",
                email="trader@example.com",
                email_verified=False,
                display_name="Trader",
            )
        )
    )
    assert result.status == "denied"
    assert result.error_code == "email_unverified"


def test_past_session_expiry_is_denied_without_token_generation() -> None:
    repo = FakeIdentityRepository()
    service = IdentityService(
        repository=repo,
        clock=lambda: NOW,
        token_factory=lambda: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    result = service.issue_session(
        user_id=uuid4(), tenant_id=uuid4(), expires_at=NOW - timedelta(seconds=1)
    )
    assert isinstance(result, CreateSessionResult)
    assert result.status == "denied"
    assert result.error_code == "invalid_expiry"


def test_validate_csrf_hashes_browser_token() -> None:
    repo = FakeIdentityRepository()
    service = IdentityService(repository=repo, clock=lambda: NOW)
    session_id = uuid4()
    assert service.validate_csrf(session_id, "csrf-token") is True
    assert repo.csrf_hash == hash_session_token("csrf-token")


def test_session_management_marks_current_and_scopes_mutations() -> None:
    repo = FakeIdentityRepository()
    current_id = uuid4()
    other_id = uuid4()
    user_id = uuid4()
    tenant_id = uuid4()
    repo.sessions = (
        SessionRecord(
            id=current_id,
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=8),
        ),
        SessionRecord(
            id=other_id,
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=7),
        ),
    )
    principal = Principal(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=current_id,
        roles=frozenset({Role.OWNER}),
    )
    service = IdentityService(repository=repo, clock=lambda: NOW)

    listed = service.list_sessions(principal)
    denied = service.revoke_session_for_principal(principal, current_id)
    revoked = service.revoke_session_for_principal(principal, other_id)
    revoked_others = service.revoke_other_sessions(principal)

    assert [session.is_current for session in listed.sessions] == [True, False]
    assert denied.status == "denied"
    assert denied.error_code == "current_session"
    assert revoked.status == "revoked"
    assert repo.revoked_user_session == other_id
    assert revoked_others.revoked_count == 2
    assert repo.revoked_other_current == current_id
