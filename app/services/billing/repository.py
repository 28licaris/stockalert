"""PostgreSQL persistence for tenant subscription state.

Shares the identity engine/session factory and the `subscriptions` table. The
billing service never imports SQLAlchemy; it talks to this repository through
plain methods returning Pydantic records.
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.services.billing.schemas import SubscriptionRecord, SubscriptionSync
from app.services.identity.models import (
    MembershipModel,
    SubscriptionModel,
    UserModel,
)


logger = logging.getLogger(__name__)


class BillingRepositoryError(RuntimeError):
    """Raised when a subscription mutation cannot be persisted."""


class SubscriptionRepository:
    def __init__(self, *, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_settings(cls) -> "SubscriptionRepository":
        from app.db.postgres import get_identity_session_factory

        return cls(session_factory=get_identity_session_factory())

    @staticmethod
    def _to_record(model: SubscriptionModel) -> SubscriptionRecord:
        return SubscriptionRecord(
            tenant_id=model.tenant_id,
            stripe_customer_id=model.stripe_customer_id,
            stripe_subscription_id=model.stripe_subscription_id,
            status=model.status,
            price_id=model.price_id,
            current_period_end=model.current_period_end,
            cancel_at_period_end=model.cancel_at_period_end,
        )

    def get_by_tenant(self, tenant_id: UUID) -> SubscriptionRecord | None:
        with self._session_factory() as db:
            model = db.scalar(
                select(SubscriptionModel).where(
                    SubscriptionModel.tenant_id == tenant_id
                )
            )
            return self._to_record(model) if model is not None else None

    def tenant_owner_email(self, tenant_id: UUID) -> str | None:
        with self._session_factory() as db:
            return db.scalar(
                select(UserModel.email)
                .join(
                    MembershipModel, MembershipModel.user_id == UserModel.id
                )
                .where(
                    MembershipModel.tenant_id == tenant_id,
                    MembershipModel.role == "owner",
                )
                .limit(1)
            )

    def ensure_customer(self, tenant_id: UUID, stripe_customer_id: str) -> None:
        """Create the tenant's billing row at first checkout (idempotent)."""
        with self._session_factory() as db:
            existing = db.scalar(
                select(SubscriptionModel).where(
                    SubscriptionModel.tenant_id == tenant_id
                )
            )
            if existing is not None:
                return
            db.add(
                SubscriptionModel(
                    tenant_id=tenant_id,
                    stripe_customer_id=stripe_customer_id,
                    status="none",
                )
            )
            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                logger.exception("billing customer row creation failed")
                raise BillingRepositoryError("could not persist customer") from exc

    def apply_subscription(self, sync: SubscriptionSync) -> bool:
        """Upsert subscription fields keyed by Stripe customer id.

        Idempotent: replaying the same webhook event yields the same row.
        Returns True if a row was updated, False if no matching customer exists.
        """
        with self._session_factory() as db:
            model = db.scalar(
                select(SubscriptionModel).where(
                    SubscriptionModel.stripe_customer_id == sync.stripe_customer_id
                )
            )
            if model is None:
                logger.warning(
                    "billing webhook for unknown customer_id=%s",
                    sync.stripe_customer_id,
                )
                return False
            model.stripe_subscription_id = sync.stripe_subscription_id
            model.status = sync.status
            model.price_id = sync.price_id
            model.current_period_end = sync.current_period_end
            model.cancel_at_period_end = sync.cancel_at_period_end
            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                logger.exception("billing subscription sync failed")
                raise BillingRepositoryError("could not persist subscription") from exc
            return True
