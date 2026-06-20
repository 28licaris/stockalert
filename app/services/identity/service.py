"""Customer identity orchestration independent of Cognito and SQLAlchemy."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from app.services.identity.contract import IdentityRepository
from app.services.identity.schemas import (
    CurrentUserResponse,
    CreateSessionCommand,
    CreateSessionResult,
    IssuedSession,
    Principal,
    ProvisionAccountCommand,
    ProvisionAccountResult,
    RevokeSessionResult,
)
from app.services.identity.security import generate_session_token, hash_session_token


class IdentityService:
    """Coordinates account and session operations through injected contracts."""

    def __init__(
        self,
        *,
        repository: IdentityRepository,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] = generate_session_token,
        csrf_token_factory: Callable[[], str] = generate_session_token,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory
        self._csrf_token_factory = csrf_token_factory

    def provision_personal_account(
        self, command: ProvisionAccountCommand
    ) -> ProvisionAccountResult:
        if not command.identity.email_verified:
            return ProvisionAccountResult(
                status="denied",
                error_code="email_unverified",
                message="a verified email is required",
            )
        return self._repository.provision_personal_account(command)

    def issue_session(
        self, *, user_id: UUID, tenant_id: UUID, expires_at: datetime
    ) -> IssuedSession | CreateSessionResult:
        now = self._clock()
        if expires_at <= now:
            return CreateSessionResult(
                status="denied",
                error_code="invalid_expiry",
                message="session expiry must be in the future",
            )
        token = self._token_factory()
        csrf_token = self._csrf_token_factory()
        result = self._repository.create_session(
            CreateSessionCommand(
                user_id=user_id,
                tenant_id=tenant_id,
                token_hash=hash_session_token(token),
                csrf_token_hash=hash_session_token(csrf_token),
                expires_at=expires_at,
            )
        )
        if result.status != "created" or result.session is None:
            return result
        return IssuedSession(
            token=token,
            csrf_token=csrf_token,
            session=result.session,
        )

    def authenticate_session(self, token: str) -> Principal | None:
        if not token:
            return None
        return self._repository.get_principal_by_token_hash(
            hash_session_token(token), now=self._clock()
        )

    def revoke_session(self, session_id: UUID) -> RevokeSessionResult:
        return self._repository.revoke_session(session_id, now=self._clock())

    def validate_csrf(self, session_id: UUID, csrf_token: str) -> bool:
        if not csrf_token:
            return False
        return self._repository.session_matches_csrf(
            session_id, hash_session_token(csrf_token)
        )

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None:
        return self._repository.get_current_user(principal)
