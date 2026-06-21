"""Stripe API boundary.

A narrow Protocol isolates the SDK so the service is unit-testable with a fake,
and so the only place that imports `stripe` is here (lazily).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID


class StripeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StripeGateway(Protocol):
    def create_customer(self, *, email: str | None, tenant_id: UUID) -> str: ...

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        trial_days: int,
        success_url: str,
        cancel_url: str,
        tenant_id: UUID,
    ) -> str: ...

    def create_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> str: ...

    def parse_webhook_event(self, payload: bytes, sig_header: str) -> dict: ...


def _epoch_to_dt(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


class StripeApiGateway:
    """Concrete gateway backed by the Stripe SDK."""

    def __init__(self, *, api_key: str, webhook_secret: str) -> None:
        if not api_key:
            raise ValueError("STRIPE_SECRET_KEY is required")
        import stripe

        self._stripe = stripe
        self._client = stripe.StripeClient(api_key)
        self._webhook_secret = webhook_secret

    def create_customer(self, *, email: str | None, tenant_id: UUID) -> str:
        try:
            customer = self._client.customers.create(
                params={
                    "email": email,
                    "metadata": {"tenant_id": str(tenant_id)},
                }
            )
            return customer.id
        except self._stripe.StripeError as exc:
            raise StripeError("customer_create_failed", str(exc)) from exc

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        trial_days: int,
        success_url: str,
        cancel_url: str,
        tenant_id: UUID,
    ) -> str:
        try:
            session = self._client.checkout.sessions.create(
                params={
                    "mode": "subscription",
                    "customer": customer_id,
                    "line_items": [{"price": price_id, "quantity": 1}],
                    "subscription_data": {"trial_period_days": trial_days}
                    if trial_days > 0
                    else {},
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "client_reference_id": str(tenant_id),
                    "allow_promotion_codes": True,
                }
            )
            if not session.url:
                raise StripeError("checkout_no_url", "Stripe returned no checkout URL")
            return session.url
        except self._stripe.StripeError as exc:
            raise StripeError("checkout_create_failed", str(exc)) from exc

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        try:
            session = self._client.billing_portal.sessions.create(
                params={"customer": customer_id, "return_url": return_url}
            )
            return session.url
        except self._stripe.StripeError as exc:
            raise StripeError("portal_create_failed", str(exc)) from exc

    def parse_webhook_event(self, payload: bytes, sig_header: str) -> dict:
        if not self._webhook_secret:
            raise StripeError("webhook_not_configured", "Webhook secret is not set")
        try:
            event = self._stripe.Webhook.construct_event(
                payload, sig_header, self._webhook_secret
            )
        except (ValueError, self._stripe.SignatureVerificationError) as exc:
            raise StripeError("webhook_signature_invalid", str(exc)) from exc
        return dict(event)


def subscription_fields(obj: dict) -> dict:
    """Extract the fields we persist from a Stripe subscription object."""
    items = (obj.get("items") or {}).get("data") or []
    first = items[0] if items else {}
    price_id = (first.get("price") or {}).get("id")
    # Stripe removed the top-level current_period_end in API 2025-03-31+; it now
    # lives per subscription item. Read the legacy field first, then fall back.
    period_end = obj.get("current_period_end") or first.get("current_period_end")
    return {
        "stripe_customer_id": obj.get("customer"),
        "stripe_subscription_id": obj.get("id"),
        "status": obj.get("status"),
        "price_id": price_id,
        "current_period_end": _epoch_to_dt(period_end),
        "cancel_at_period_end": bool(obj.get("cancel_at_period_end", False)),
    }
