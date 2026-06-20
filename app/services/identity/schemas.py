"""Pydantic contracts for customer identity, tenancy, and sessions."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class TenantStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class TenantKind(StrEnum):
    PERSONAL = "personal"
    ORGANIZATION = "organization"


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"
    SUPPORT = "support"
    DEVELOPER = "developer"


class ExternalIdentityClaim(BaseModel):
    """Verified identity returned by an external identity provider."""

    model_config = ConfigDict(str_strip_whitespace=True)

    provider: str = Field(min_length=1, max_length=32)
    subject: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    email_verified: bool
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.lower()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.casefold()
        local, separator, domain = normalized.partition("@")
        if not separator or not local or "." not in domain:
            raise ValueError("email must be a valid address")
        return normalized


class AccountRef(BaseModel):
    user_id: UUID
    tenant_id: UUID
    role: Role


class ProvisionAccountCommand(BaseModel):
    identity: ExternalIdentityClaim


class ProvisionAccountResult(BaseModel):
    status: Literal["created", "existing", "denied", "conflict", "error"]
    account: AccountRef | None = None
    error_code: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> "ProvisionAccountResult":
        success = self.status in {"created", "existing"}
        if success and self.account is None:
            raise ValueError("successful provisioning requires account")
        if not success and not self.error_code:
            raise ValueError("failed provisioning requires error_code")
        return self


class CreateSessionCommand(BaseModel):
    user_id: UUID
    tenant_id: UUID
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    csrf_token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    provider_session_ciphertext: bytes | None = Field(default=None, repr=False)

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value


class SessionRecord(BaseModel):
    id: UUID
    user_id: UUID
    tenant_id: UUID
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None


class CreateSessionResult(BaseModel):
    status: Literal["created", "denied", "error"]
    session: SessionRecord | None = None
    error_code: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> "CreateSessionResult":
        if self.status == "created" and self.session is None:
            raise ValueError("created session result requires session")
        if self.status != "created" and not self.error_code:
            raise ValueError("failed session result requires error_code")
        return self


class IssuedSession(BaseModel):
    """One-time session material returned only to the HTTP cookie boundary."""

    token: SecretStr = Field(repr=False)
    csrf_token: SecretStr = Field(repr=False)
    session: SessionRecord


class Principal(BaseModel):
    """Server-derived authorization context for one authenticated request."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    tenant_id: UUID
    session_id: UUID
    roles: frozenset[Role]
    permissions: frozenset[str] = frozenset()
    entitlements: frozenset[str] = frozenset()


class RevokeSessionResult(BaseModel):
    status: Literal["revoked", "already_revoked", "not_found", "denied", "error"]
    error_code: str | None = None
    message: str | None = None


class RevokeSessionsResult(BaseModel):
    status: Literal["revoked", "error"]
    revoked_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    message: str | None = None


class SessionSummary(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    is_current: bool


class SessionListResponse(BaseModel):
    sessions: tuple[SessionSummary, ...]


class SessionRevocationResponse(BaseModel):
    revoked_count: int = Field(ge=0)


class CreateLoginTransactionCommand(BaseModel):
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: SecretStr = Field(repr=False, min_length=32)
    code_verifier: SecretStr = Field(repr=False, min_length=43, max_length=128)
    return_to: str = Field(min_length=1, max_length=500)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value


class LoginTransaction(BaseModel):
    id: UUID
    nonce: SecretStr = Field(repr=False)
    code_verifier: SecretStr = Field(repr=False)
    return_to: str
    expires_at: datetime


class CreateLoginTransactionResult(BaseModel):
    status: Literal["created", "error"]
    transaction_id: UUID | None = None
    error_code: str | None = None


class ConsumeLoginTransactionResult(BaseModel):
    status: Literal["consumed", "not_found", "expired", "replayed", "error"]
    transaction: LoginTransaction | None = None
    error_code: str | None = None


class CognitoTokenSet(BaseModel):
    """Validated Cognito callback result; tokens remain process-local."""

    identity: ExternalIdentityClaim
    access_token: SecretStr = Field(repr=False)
    id_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr | None = Field(default=None, repr=False)
    expires_in: int = Field(gt=0)


class CognitoOAuthConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    domain: str
    issuer_url: str
    client_id: str = Field(min_length=1)
    client_secret: SecretStr | None = Field(default=None, repr=False)
    scopes: tuple[str, ...] = ("openid", "email", "profile")

    @field_validator("domain", "issuer_url")
    @classmethod
    def require_https_origin(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("Cognito origins must use HTTPS")
        return normalized


class BeginLoginResult(BaseModel):
    status: Literal["ok", "error"]
    authorization_url: str | None = None
    error_code: str | None = None


class CompleteLoginResult(BaseModel):
    status: Literal[
        "ok", "invalid_state", "expired", "replayed", "identity_conflict", "error"
    ]
    issued_session: IssuedSession | None = None
    return_to: str | None = None
    error_code: str | None = None


class CurrentUserResponse(BaseModel):
    user_id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    roles: frozenset[Role]
    permissions: frozenset[str]
    entitlements: frozenset[str]


class LogoutResponse(BaseModel):
    redirect_url: str
