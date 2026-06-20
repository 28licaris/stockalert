"""Public Protocols for the customer identity service."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.services.identity.schemas import (
    CognitoTokenSet,
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
    ) -> str: ...

    async def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> CognitoTokenSet: ...

    def logout_url(self, *, logout_uri: str) -> str: ...


@runtime_checkable
class IdentityServiceProtocol(Protocol):
    def provision_personal_account(
        self, command: ProvisionAccountCommand
    ) -> ProvisionAccountResult: ...

    def issue_session(
        self, *, user_id: UUID, tenant_id: UUID, expires_at: datetime
    ) -> IssuedSession | CreateSessionResult: ...

    def authenticate_session(self, token: str) -> Principal | None: ...

    def revoke_session(self, session_id: UUID) -> RevokeSessionResult: ...

    def validate_csrf(self, session_id: UUID, csrf_token: str) -> bool: ...

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None: ...
