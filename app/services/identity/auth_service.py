"""OAuth login orchestration over provider and repository contracts."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.services.identity.contract import (
    IdentityProvider,
    IdentityProviderError,
    IdentityRepository,
)
from app.services.identity.schemas import (
    BeginLoginResult,
    CompleteLoginResult,
    CreateLoginTransactionCommand,
    CreateSessionResult,
    ProvisionAccountCommand,
    Principal,
    RevokeSessionResult,
    SecurityEventType,
)
from app.services.identity.security import (
    generate_oauth_nonce,
    generate_oauth_state,
    generate_pkce_verifier,
    hash_session_token,
    pkce_s256_challenge,
    safe_return_path,
)
from app.services.identity.service import IdentityService


class OAuthAuthenticationService:
    def __init__(
        self,
        *,
        provider: IdentityProvider,
        repository: IdentityRepository,
        identity_service: IdentityService,
        redirect_uri: str,
        logout_uri: str,
        session_ttl: timedelta,
        transaction_ttl: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
        state_factory: Callable[[], str] = generate_oauth_state,
        nonce_factory: Callable[[], str] = generate_oauth_nonce,
        verifier_factory: Callable[[], str] = generate_pkce_verifier,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._identity_service = identity_service
        self._redirect_uri = redirect_uri
        self._logout_uri = logout_uri
        self._session_ttl = session_ttl
        self._transaction_ttl = transaction_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._state_factory = state_factory
        self._nonce_factory = nonce_factory
        self._verifier_factory = verifier_factory

    async def begin_login(
        self,
        *,
        return_to: str | None,
        identity_provider: str | None = None,
        screen_hint: str | None = None,
        prompt: str | None = None,
    ) -> BeginLoginResult:
        state = self._state_factory()
        nonce = self._nonce_factory()
        verifier = self._verifier_factory()
        command = CreateLoginTransactionCommand(
            state_hash=hash_session_token(state),
            nonce=nonce,
            code_verifier=verifier,
            return_to=safe_return_path(return_to),
            expires_at=self._clock() + self._transaction_ttl,
        )
        created = await asyncio.to_thread(
            self._repository.create_login_transaction, command
        )
        if created.status != "created":
            return BeginLoginResult(status="error", error_code=created.error_code)
        return BeginLoginResult(
            status="ok",
            authorization_url=self._provider.authorization_url(
                state=state,
                nonce=nonce,
                code_challenge=pkce_s256_challenge(verifier),
                redirect_uri=self._redirect_uri,
                identity_provider=identity_provider,
                screen_hint=screen_hint,
                prompt=prompt,
            ),
        )

    async def complete_login(self, *, code: str, state: str) -> CompleteLoginResult:
        consumed = await asyncio.to_thread(
            self._repository.consume_login_transaction,
            hash_session_token(state),
            now=self._clock(),
        )
        if consumed.status == "not_found":
            return CompleteLoginResult(status="invalid_state", error_code="invalid_state")
        if consumed.status == "expired":
            return CompleteLoginResult(status="expired", error_code="login_expired")
        if consumed.status == "replayed":
            return CompleteLoginResult(status="replayed", error_code="state_replayed")
        if consumed.status != "consumed" or consumed.transaction is None:
            return CompleteLoginResult(status="error", error_code=consumed.error_code)

        transaction = consumed.transaction
        try:
            token_set = await self._provider.exchange_code(
                code,
                redirect_uri=self._redirect_uri,
                code_verifier=transaction.code_verifier.get_secret_value(),
                expected_nonce=transaction.nonce.get_secret_value(),
            )
        except IdentityProviderError as exc:
            return CompleteLoginResult(status="error", error_code=exc.code)

        provisioned = await asyncio.to_thread(
            self._identity_service.provision_personal_account,
            ProvisionAccountCommand(identity=token_set.identity),
        )
        if provisioned.status == "conflict":
            return CompleteLoginResult(
                status="identity_conflict", error_code=provisioned.error_code
            )
        if provisioned.account is None:
            return CompleteLoginResult(status="error", error_code=provisioned.error_code)

        issued = await asyncio.to_thread(
            self._identity_service.issue_session,
            user_id=provisioned.account.user_id,
            tenant_id=provisioned.account.tenant_id,
            expires_at=self._clock() + self._session_ttl,
        )
        if isinstance(issued, CreateSessionResult):
            return CompleteLoginResult(status="error", error_code=issued.error_code)
        audit = await asyncio.to_thread(
            self._identity_service.record_security_event,
            Principal(
                user_id=provisioned.account.user_id,
                tenant_id=provisioned.account.tenant_id,
                session_id=issued.session.id,
                roles=frozenset({provisioned.account.role}),
            ),
            SecurityEventType.LOGIN_SUCCEEDED,
        )
        if audit.status != "created":
            await asyncio.to_thread(
                self._identity_service.revoke_session, issued.session.id
            )
            return CompleteLoginResult(status="error", error_code="audit_unavailable")
        return CompleteLoginResult(
            status="ok",
            issued_session=issued,
            return_to=transaction.return_to,
        )

    def logout_url(self) -> str:
        return self._provider.logout_url(logout_uri=self._logout_uri)

    def password_reset_url(self) -> str:
        return self._provider.password_reset_url(redirect_uri=self._redirect_uri)

    def revoke_session(self, session_id: UUID) -> RevokeSessionResult:
        return self._identity_service.revoke_session(session_id)
