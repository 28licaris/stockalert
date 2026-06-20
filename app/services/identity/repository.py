"""PostgreSQL implementation of the identity repository contract."""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.services.identity.models import (
    AuthTransactionModel,
    ExternalIdentityModel,
    MembershipModel,
    SessionModel,
    TenantModel,
    UserModel,
)
from app.services.identity.schemas import (
    AccountRef,
    ConsumeLoginTransactionResult,
    CreateLoginTransactionCommand,
    CreateLoginTransactionResult,
    CreateSessionCommand,
    CreateSessionResult,
    CurrentUserResponse,
    Principal,
    ProvisionAccountCommand,
    ProvisionAccountResult,
    RevokeSessionResult,
    RevokeSessionsResult,
    LoginTransaction,
    Role,
    SessionRecord,
)

logger = logging.getLogger(__name__)


class PostgresIdentityRepository:
    """Transactional PostgreSQL repository; ORM models never escape it."""

    def __init__(self, *, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_settings(cls) -> "PostgresIdentityRepository":
        from app.db.postgres import get_identity_session_factory

        return cls(session_factory=get_identity_session_factory())

    def provision_personal_account(
        self, command: ProvisionAccountCommand
    ) -> ProvisionAccountResult:
        identity = command.identity
        with self._session_factory() as db:
            existing = self._find_account(db, identity.provider, identity.subject)
            if existing is not None:
                return ProvisionAccountResult(status="existing", account=existing)

            email_owner = db.scalar(
                select(UserModel.id).where(UserModel.normalized_email == identity.email)
            )
            if email_owner is not None:
                return ProvisionAccountResult(
                    status="conflict",
                    error_code="identity_link_required",
                    message="an account already exists for this verified email",
                )

            user = UserModel(
                email=identity.email,
                normalized_email=identity.email,
                display_name=identity.display_name,
                status="active",
            )
            tenant = TenantModel(
                name=f"{identity.display_name}'s account",
                kind="personal",
                status="active",
            )
            try:
                db.add_all((user, tenant))
                db.flush()
                membership = MembershipModel(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role=Role.OWNER.value,
                    status="active",
                )
                external = ExternalIdentityModel(
                    user_id=user.id,
                    provider=identity.provider,
                    provider_subject=identity.subject,
                    email_at_link=identity.email,
                )
                db.add_all((membership, external))
                db.commit()
            except IntegrityError:
                db.rollback()
                # Concurrent callback replay may have created the same external
                # identity. Re-read it and return idempotently when possible.
                raced = self._find_account(db, identity.provider, identity.subject)
                if raced is not None:
                    return ProvisionAccountResult(status="existing", account=raced)
                logger.exception("identity provisioning constraint conflict")
                return ProvisionAccountResult(
                    status="conflict",
                    error_code="identity_conflict",
                    message="identity could not be linked safely",
                )
            except SQLAlchemyError:
                db.rollback()
                logger.exception("identity provisioning database error")
                return ProvisionAccountResult(
                    status="error",
                    error_code="database_error",
                    message="account provisioning failed",
                )

            return ProvisionAccountResult(
                status="created",
                account=AccountRef(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER),
            )

    def create_session(self, command: CreateSessionCommand) -> CreateSessionResult:
        with self._session_factory() as db:
            membership = db.scalar(
                select(MembershipModel).join(UserModel).join(TenantModel).where(
                    MembershipModel.user_id == command.user_id,
                    MembershipModel.tenant_id == command.tenant_id,
                    MembershipModel.status == "active",
                    UserModel.status == "active",
                    TenantModel.status == "active",
                )
            )
            if membership is None:
                return CreateSessionResult(
                    status="denied",
                    error_code="account_inactive",
                    message="user or tenant is not active",
                )

            model = SessionModel(
                user_id=command.user_id,
                tenant_id=command.tenant_id,
                token_hash=command.token_hash,
                csrf_token_hash=command.csrf_token_hash,
                expires_at=command.expires_at,
                provider_session_ciphertext=command.provider_session_ciphertext,
            )
            try:
                db.add(model)
                db.commit()
                db.refresh(model)
            except SQLAlchemyError:
                db.rollback()
                logger.exception("identity session creation database error")
                return CreateSessionResult(
                    status="error",
                    error_code="database_error",
                    message="session creation failed",
                )
            return CreateSessionResult(status="created", session=self._session_record(model))

    def get_principal_by_token_hash(
        self, token_hash: str, *, now: datetime
    ) -> Principal | None:
        with self._session_factory() as db:
            row = db.execute(
                select(SessionModel, MembershipModel)
                .join(
                    MembershipModel,
                    (MembershipModel.user_id == SessionModel.user_id)
                    & (MembershipModel.tenant_id == SessionModel.tenant_id),
                )
                .join(UserModel, UserModel.id == SessionModel.user_id)
                .join(TenantModel, TenantModel.id == SessionModel.tenant_id)
                .where(
                    SessionModel.token_hash == token_hash,
                    SessionModel.revoked_at.is_(None),
                    SessionModel.expires_at > now,
                    MembershipModel.status == "active",
                    UserModel.status == "active",
                    TenantModel.status == "active",
                )
            ).one_or_none()
            if row is None:
                return None
            session, membership = row
            return Principal(
                user_id=session.user_id,
                tenant_id=session.tenant_id,
                session_id=session.id,
                roles=frozenset({Role(membership.role)}),
            )

    def revoke_session(self, session_id: UUID, *, now: datetime) -> RevokeSessionResult:
        with self._session_factory() as db:
            model = db.get(SessionModel, session_id)
            if model is None:
                return RevokeSessionResult(status="not_found")
            if model.revoked_at is not None:
                return RevokeSessionResult(status="already_revoked")
            model.revoked_at = now
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                logger.exception("identity session revocation database error")
                return RevokeSessionResult(
                    status="error",
                    error_code="database_error",
                    message="session revocation failed",
                )
            return RevokeSessionResult(status="revoked")

    def list_active_sessions(
        self, *, user_id: UUID, tenant_id: UUID, now: datetime
    ) -> tuple[SessionRecord, ...]:
        with self._session_factory() as db:
            models = db.scalars(
                select(SessionModel)
                .where(
                    SessionModel.user_id == user_id,
                    SessionModel.tenant_id == tenant_id,
                    SessionModel.revoked_at.is_(None),
                    SessionModel.expires_at > now,
                )
                .order_by(SessionModel.created_at.desc(), SessionModel.id)
            ).all()
            return tuple(self._session_record(model) for model in models)

    def revoke_user_session(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> RevokeSessionResult:
        with self._session_factory() as db:
            model = db.scalar(
                select(SessionModel).where(
                    SessionModel.id == session_id,
                    SessionModel.user_id == user_id,
                    SessionModel.tenant_id == tenant_id,
                )
            )
            if model is None:
                return RevokeSessionResult(status="not_found")
            if model.revoked_at is not None:
                return RevokeSessionResult(status="already_revoked")
            model.revoked_at = now
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                logger.exception("managed identity session revocation database error")
                return RevokeSessionResult(
                    status="error",
                    error_code="database_error",
                    message="session revocation failed",
                )
            return RevokeSessionResult(status="revoked")

    def revoke_other_sessions(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        current_session_id: UUID,
        now: datetime,
    ) -> RevokeSessionsResult:
        with self._session_factory() as db:
            try:
                result = db.execute(
                    update(SessionModel)
                    .where(
                        SessionModel.user_id == user_id,
                        SessionModel.tenant_id == tenant_id,
                        SessionModel.id != current_session_id,
                        SessionModel.revoked_at.is_(None),
                        SessionModel.expires_at > now,
                    )
                    .values(revoked_at=now)
                )
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                logger.exception("bulk identity session revocation database error")
                return RevokeSessionsResult(
                    status="error",
                    error_code="database_error",
                    message="sessions could not be revoked",
                )
            return RevokeSessionsResult(
                status="revoked", revoked_count=max(result.rowcount or 0, 0)
            )

    def create_login_transaction(
        self, command: CreateLoginTransactionCommand
    ) -> CreateLoginTransactionResult:
        model = AuthTransactionModel(
            state_hash=command.state_hash,
            nonce=command.nonce.get_secret_value(),
            code_verifier=command.code_verifier.get_secret_value(),
            return_to=command.return_to,
            expires_at=command.expires_at,
        )
        with self._session_factory() as db:
            try:
                db.add(model)
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                logger.exception("identity login transaction creation failed")
                return CreateLoginTransactionResult(
                    status="error", error_code="database_error"
                )
            return CreateLoginTransactionResult(
                status="created", transaction_id=model.id
            )

    def consume_login_transaction(
        self, state_hash: str, *, now: datetime
    ) -> ConsumeLoginTransactionResult:
        with self._session_factory() as db:
            try:
                row = db.execute(
                    update(AuthTransactionModel)
                    .where(
                        AuthTransactionModel.state_hash == state_hash,
                        AuthTransactionModel.consumed_at.is_(None),
                        AuthTransactionModel.expires_at > now,
                    )
                    .values(consumed_at=now)
                    .returning(AuthTransactionModel)
                ).scalar_one_or_none()
                if row is not None:
                    result = ConsumeLoginTransactionResult(
                        status="consumed",
                        transaction=LoginTransaction(
                            id=row.id,
                            nonce=row.nonce,
                            code_verifier=row.code_verifier,
                            return_to=row.return_to,
                            expires_at=row.expires_at,
                        ),
                    )
                    db.commit()
                    return result

                existing = db.scalar(
                    select(AuthTransactionModel).where(
                        AuthTransactionModel.state_hash == state_hash
                    )
                )
            except SQLAlchemyError:
                db.rollback()
                logger.exception("identity login transaction consumption failed")
                return ConsumeLoginTransactionResult(
                    status="error", error_code="database_error"
                )

            if existing is None:
                return ConsumeLoginTransactionResult(status="not_found")
            if existing.consumed_at is not None:
                return ConsumeLoginTransactionResult(status="replayed")
            return ConsumeLoginTransactionResult(status="expired")

    def session_matches_csrf(self, session_id: UUID, csrf_token_hash: str) -> bool:
        with self._session_factory() as db:
            match = db.scalar(
                select(SessionModel.id).where(
                    SessionModel.id == session_id,
                    SessionModel.csrf_token_hash == csrf_token_hash,
                    SessionModel.revoked_at.is_(None),
                )
            )
            return match is not None

    def get_current_user(self, principal: Principal) -> CurrentUserResponse | None:
        with self._session_factory() as db:
            user = db.scalar(
                select(UserModel).where(
                    UserModel.id == principal.user_id,
                    UserModel.status == "active",
                )
            )
            if user is None:
                return None
            return CurrentUserResponse(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                email=user.email,
                display_name=user.display_name,
                roles=principal.roles,
                permissions=principal.permissions,
                entitlements=principal.entitlements,
            )

    @staticmethod
    def _find_account(db: Session, provider: str, subject: str) -> AccountRef | None:
        row = db.execute(
            select(UserModel.id, MembershipModel.tenant_id, MembershipModel.role)
            .join(ExternalIdentityModel, ExternalIdentityModel.user_id == UserModel.id)
            .join(MembershipModel, MembershipModel.user_id == UserModel.id)
            .join(TenantModel, TenantModel.id == MembershipModel.tenant_id)
            .where(
                ExternalIdentityModel.provider == provider,
                ExternalIdentityModel.provider_subject == subject,
                MembershipModel.status == "active",
                TenantModel.kind == "personal",
            )
        ).first()
        if row is None:
            return None
        return AccountRef(user_id=row.id, tenant_id=row.tenant_id, role=Role(row.role))

    @staticmethod
    def _session_record(model: SessionModel) -> SessionRecord:
        return SessionRecord(
            id=model.id,
            user_id=model.user_id,
            tenant_id=model.tenant_id,
            created_at=model.created_at,
            expires_at=model.expires_at,
            last_seen_at=model.last_seen_at,
            revoked_at=model.revoked_at,
        )
