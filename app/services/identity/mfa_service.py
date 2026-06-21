"""Provider-neutral TOTP MFA orchestration for authenticated customers."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from app.services.identity.contract import (
    IdentityProvider,
    IdentityProviderError,
    IdentityRepository,
)
from app.services.identity.provider_session import (
    ProviderSessionCipher,
    ProviderSessionMaterial,
)
from app.services.identity.schemas import (
    MfaEnrollmentResponse,
    MfaStatusResponse,
    MfaVerificationResponse,
    Principal,
    SecurityEventType,
)
from app.services.identity.service import IdentityService


logger = logging.getLogger(__name__)


class MfaServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MfaService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        provider: IdentityProvider,
        cipher: ProviderSessionCipher,
        identity_service: IdentityService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._cipher = cipher
        self._identity_service = identity_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def status(self, principal: Principal) -> MfaStatusResponse:
        material = self._material(principal, allow_expired=True)
        if material is None:
            return MfaStatusResponse(
                supported=True,
                enabled=False,
                preferred=False,
                reauthentication_required=True,
            )
        if material.source_provider != "cognito":
            return MfaStatusResponse(supported=False, enabled=False, preferred=False)
        if material.expires_at <= self._clock():
            return MfaStatusResponse(
                supported=True,
                enabled=False,
                preferred=False,
                reauthentication_required=True,
            )
        try:
            enabled, preferred = self._provider.get_mfa_status(
                access_token=material.access_token.get_secret_value()
            )
        except IdentityProviderError as exc:
            logger.warning(
                "MFA status lookup failed user_id=%s code=%s",
                principal.user_id,
                exc.code,
            )
            raise MfaServiceError(exc.code, "Unable to load MFA status") from exc
        return MfaStatusResponse(
            supported=True, enabled=enabled, preferred=preferred
        )

    def begin_enrollment(self, principal: Principal) -> MfaEnrollmentResponse:
        material = self._required_material(principal)
        if material.source_provider != "cognito":
            raise MfaServiceError("mfa_not_supported", "MFA is managed by your identity provider")
        try:
            secret = self._provider.associate_software_token(
                access_token=material.access_token.get_secret_value()
            )
        except IdentityProviderError as exc:
            logger.warning(
                "MFA enrollment association failed user_id=%s code=%s",
                principal.user_id,
                exc.code,
            )
            raise MfaServiceError(exc.code, "Unable to begin MFA enrollment") from exc
        current = self._repository.get_current_user(principal)
        label = current.email if current is not None else str(principal.user_id)
        path = quote(f"StockAlert:{label}", safe="")
        query = urlencode({"secret": secret, "issuer": "StockAlert"})
        return MfaEnrollmentResponse(
            secret_code=secret,
            otpauth_uri=f"otpauth://totp/{path}?{query}",
        )

    def verify_enrollment(
        self, principal: Principal, code: str
    ) -> MfaVerificationResponse:
        material = self._required_material(principal)
        try:
            verified = self._provider.verify_software_token(
                access_token=material.access_token.get_secret_value(), code=code
            )
        except IdentityProviderError as exc:
            logger.warning(
                "MFA verification failed user_id=%s code=%s",
                principal.user_id,
                exc.code,
            )
            raise MfaServiceError(exc.code, "Unable to verify MFA code") from exc
        if not verified:
            logger.info("MFA verification rejected invalid code user_id=%s", principal.user_id)
            raise MfaServiceError("invalid_mfa_code", "The verification code is invalid")
        audit = self._identity_service.record_security_event(
            principal, SecurityEventType.MFA_ENABLED
        )
        if audit.status != "created":
            logger.error(
                "MFA enabled but audit record failed user_id=%s status=%s",
                principal.user_id,
                audit.status,
            )
            raise MfaServiceError("audit_unavailable", "MFA audit could not be recorded")
        logger.info(
            "MFA TOTP enabled user_id=%s tenant_id=%s",
            principal.user_id,
            principal.tenant_id,
        )
        return MfaVerificationResponse(enabled=True)

    def _required_material(self, principal: Principal) -> ProviderSessionMaterial:
        material = self._material(principal, allow_expired=False)
        if material is None:
            raise MfaServiceError("reauthentication_required", "Sign in again to manage MFA")
        return material

    def _material(
        self, principal: Principal, *, allow_expired: bool
    ) -> ProviderSessionMaterial | None:
        ciphertext = self._repository.get_provider_session_ciphertext(principal)
        if not ciphertext:
            return None
        try:
            material = self._cipher.decrypt(ciphertext)
        except (ValueError, TypeError) as exc:
            raise MfaServiceError("provider_session_invalid", "Provider session cannot be read") from exc
        if not allow_expired and material.expires_at <= self._clock():
            return None
        return material
