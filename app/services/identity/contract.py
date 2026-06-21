"""Public Protocols for the customer identity service."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.services.identity.schemas import (
    CognitoTokenSet,
    CreateSecurityEventCommand,
    CreateSecurityEventResult,
    CurrentUserResponse,
    ConsumeLoginTransactionResult,
    CreateLoginTransactionCommand,
    CreateLoginTransactionResult,
    CreateSessionCommand,
    CreateSessionResult,
    IssuedSession,
    Principal,
    ProvisionAccountCommand,
    ProvisionAccountResult,
    RevokeSessionResult,
    RevokeSessionsResult,
    SessionListResponse,
    SessionRecord,
    SecurityEventListResponse,
    SecurityEventRecord,
    SecurityEventType,
)


class IdentityProviderError(RuntimeError):
    """Safe provider-boundary failure; message contains no token material."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class IdentityRepository(Protocol):
    """Persistence boundary implemented by PostgreSQL in production."""

    def provision_personal_account(
        self, command: ProvisionAccountCommand
    ) -> ProvisionAccountResult: ...

    def create_session(self, command: CreateSessionCommand) -> CreateSessionResult: ...

    def get_principal_by_token_hash(
        self, token_hash: str, *, now: datetime
    ) -> Principal | None: ...

    def revoke_session(self, session_id: UUID, *, now: datetime) -> RevokeSessionResult: ...

    def list_active_sessions(
        self, *, user_id: UUID, tenant_id: UUID, now: datetime
    ) -> tuple[SessionRecord, ...]: ...

    def revoke_user_session(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> RevokeSessionResult: ...

    def revoke_other_sessions(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        current_session_id: UUID,
        now: datetime,
    ) -> RevokeSessionsResult: ...

    def create_login_transaction(
        self, command: CreateLoginTransactionCommand
    ) -> CreateLoginTransactionResult: ...

    def consume_login_transaction(
        self, state_hash: str, *, now: datetime
    ) -> ConsumeLoginTransactionResult: ...

    def session_matches_csrf(
        self, session_id: UUID, csrf_token_hash: str
    ) -> bool: ...

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None: ...

    def get_provider_session_ciphertext(self, principal: Principal) -> bytes | None: ...

    def create_security_event(
        self, command: CreateSecurityEventCommand
    ) -> CreateSecurityEventResult: ...

    def list_security_events(
        self, *, user_id: UUID, tenant_id: UUID, limit: int
    ) -> tuple[SecurityEventRecord, ...]: ...


@runtime_checkable
class IdentityProvider(Protocol):
    """Cognito seam. The concrete AWS adapter lands in the identity slice."""

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        identity_provider: str | None = None,
        screen_hint: str | None = None,
        prompt: str | None = None,
    ) -> str: ...

    async def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> CognitoTokenSet: ...

    def password_reset_url(self, *, redirect_uri: str) -> str: ...

    def logout_url(self, *, logout_uri: str) -> str: ...

    def get_mfa_status(self, *, access_token: str) -> tuple[bool, bool]: ...

    def associate_software_token(self, *, access_token: str) -> str: ...

    def verify_software_token(self, *, access_token: str, code: str) -> bool: ...


@runtime_checkable
class IdentityServiceProtocol(Protocol):
    def provision_personal_account(
        self, command: ProvisionAccountCommand
    ) -> ProvisionAccountResult: ...

    def issue_session(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        expires_at: datetime,
        provider_session_ciphertext: bytes | None = None,
    ) -> IssuedSession | CreateSessionResult: ...

    def authenticate_session(self, token: str) -> Principal | None: ...

    def revoke_session(self, session_id: UUID) -> RevokeSessionResult: ...

    def list_sessions(self, principal: Principal) -> SessionListResponse: ...

    def revoke_session_for_principal(
        self, principal: Principal, session_id: UUID
    ) -> RevokeSessionResult: ...

    def revoke_other_sessions(self, principal: Principal) -> RevokeSessionsResult: ...

    def validate_csrf(self, session_id: UUID, csrf_token: str) -> bool: ...

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None: ...

    def list_security_events(self, principal: Principal) -> SecurityEventListResponse: ...

    def record_security_event(
        self,
        principal: Principal,
        event_type: SecurityEventType,
        *,
        session_id: UUID | None = None,
    ) -> CreateSecurityEventResult: ...
