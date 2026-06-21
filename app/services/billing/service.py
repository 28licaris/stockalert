"""Subscription billing orchestration (Stripe + persistence + entitlements)."""
from __future__ import annotations

import logging
from uuid import UUID

from app.services.billing.gateway import (
    StripeError,
    StripeGateway,
    subscription_fields,
)
from app.services.billing.repository import (
    BillingRepositoryError,
    SubscriptionRepository,
)
from app.services.billing.schemas import (
    CheckoutSessionResponse,
    PortalSessionResponse,
    SubscriptionStatusResponse,
    SubscriptionSync,
)
from app.services.identity.entitlements import entitlements_for


logger = logging.getLogger(__name__)

# Stripe events that carry a full subscription object we mirror locally.
_SUBSCRIPTION_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


class BillingServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BillingService:
    def __init__(
        self,
        *,
        gateway: StripeGateway,
        repository: SubscriptionRepository,
        price_monthly: str,
        price_annual: str,
        trial_days: int,
        return_url: str,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._price_monthly = price_monthly
        self._price_annual = price_annual
        self._trial_days = trial_days
        self._return_url = return_url

    def _price_for_plan(self, plan: str) -> str:
        price = self._price_monthly if plan == "monthly" else self._price_annual
        if not price:
            raise BillingServiceError(
                "plan_not_configured", f"No Stripe price configured for {plan}"
            )
        return price

    def _plan_for_price(self, price_id: str | None) -> str | None:
        if price_id and price_id == self._price_monthly:
            return "monthly"
        if price_id and price_id == self._price_annual:
            return "annual"
        return None

    def _ensure_customer(self, tenant_id: UUID) -> str:
        existing = self._repository.get_by_tenant(tenant_id)
        if existing is not None:
            return existing.stripe_customer_id
        email = self._repository.tenant_owner_email(tenant_id)
        try:
            customer_id = self._gateway.create_customer(
                email=email, tenant_id=tenant_id
            )
        except StripeError as exc:
            logger.warning(
                "billing customer create failed tenant_id=%s code=%s",
                tenant_id,
                exc.code,
            )
            raise BillingServiceError(exc.code, "Could not start billing") from exc
        try:
            self._repository.ensure_customer(tenant_id, customer_id)
        except BillingRepositoryError as exc:
            raise BillingServiceError("billing_unavailable", str(exc)) from exc
        return customer_id

    def create_checkout_session(
        self, tenant_id: UUID, plan: str
    ) -> CheckoutSessionResponse:
        price_id = self._price_for_plan(plan)
        customer_id = self._ensure_customer(tenant_id)
        try:
            url = self._gateway.create_checkout_session(
                customer_id=customer_id,
                price_id=price_id,
                trial_days=self._trial_days,
                success_url=f"{self._return_url}?billing=success",
                cancel_url=f"{self._return_url}?billing=canceled",
                tenant_id=tenant_id,
            )
        except StripeError as exc:
            logger.warning(
                "billing checkout failed tenant_id=%s code=%s", tenant_id, exc.code
            )
            raise BillingServiceError(exc.code, "Could not open checkout") from exc
        logger.info("billing checkout started tenant_id=%s plan=%s", tenant_id, plan)
        return CheckoutSessionResponse(url=url)

    def create_portal_session(self, tenant_id: UUID) -> PortalSessionResponse:
        existing = self._repository.get_by_tenant(tenant_id)
        if existing is None:
            raise BillingServiceError(
                "no_billing_account", "No billing account exists yet"
            )
        try:
            url = self._gateway.create_portal_session(
                customer_id=existing.stripe_customer_id, return_url=self._return_url
            )
        except StripeError as exc:
            logger.warning(
                "billing portal failed tenant_id=%s code=%s", tenant_id, exc.code
            )
            raise BillingServiceError(exc.code, "Could not open billing portal") from exc
        return PortalSessionResponse(url=url)

    def get_status(self, tenant_id: UUID) -> SubscriptionStatusResponse:
        record = self._repository.get_by_tenant(tenant_id)
        status = record.status if record is not None else "none"
        price_id = record.price_id if record is not None else None
        entitlements = entitlements_for(status, price_id)
        return SubscriptionStatusResponse(
            status=status,
            active="pro" in entitlements,
            plan=self._plan_for_price(price_id),
            price_id=price_id,
            current_period_end=record.current_period_end if record else None,
            cancel_at_period_end=record.cancel_at_period_end if record else False,
            entitlements=tuple(sorted(entitlements)),
        )

    def handle_webhook(self, payload: bytes, sig_header: str) -> str:
        """Verify and apply a Stripe webhook. Returns the handled event type."""
        try:
            event = self._gateway.parse_webhook_event(payload, sig_header)
        except StripeError as exc:
            logger.warning("billing webhook rejected code=%s", exc.code)
            raise BillingServiceError(exc.code, "Webhook could not be verified") from exc

        event_type = str(event.get("type", ""))
        if event_type not in _SUBSCRIPTION_EVENTS:
            logger.info("billing webhook ignored type=%s", event_type)
            return event_type

        obj = (event.get("data") or {}).get("object") or {}
        fields = subscription_fields(obj)
        if not fields.get("stripe_customer_id") or not fields.get("status"):
            logger.warning("billing webhook missing customer/status type=%s", event_type)
            raise BillingServiceError("webhook_malformed", "Webhook payload incomplete")
        try:
            applied = self._repository.apply_subscription(SubscriptionSync(**fields))
        except BillingRepositoryError as exc:
            raise BillingServiceError("billing_unavailable", str(exc)) from exc
        logger.info(
            "billing webhook applied type=%s status=%s matched=%s",
            event_type,
            fields["status"],
            applied,
        )
        return event_type
