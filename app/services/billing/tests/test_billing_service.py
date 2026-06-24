"""Billing orchestration tests (Stripe + persistence faked)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.billing.gateway import StripeError
from app.services.billing.service import BillingService, BillingServiceError
from app.services.billing.schemas import SubscriptionRecord, SubscriptionSync


PRICE_M = "price_monthly"
PRICE_A = "price_annual"


class FakeGateway:
    def __init__(self) -> None:
        self.created_customer_for: object = None
        self.checkout_args: dict | None = None
        self.event: dict | None = None
        self.raise_on_customer = False

    def create_customer(self, *, email, tenant_id):
        if self.raise_on_customer:
            raise StripeError("customer_create_failed", "boom")
        self.created_customer_for = (email, tenant_id)
        return "cus_TEST"

    def create_checkout_session(self, **kwargs):
        self.checkout_args = kwargs
        return "https://checkout.stripe.test/session"

    def create_portal_session(self, *, customer_id, return_url):
        return f"https://portal.stripe.test/{customer_id}"

    def parse_webhook_event(self, payload, sig_header):
        if self.event is None:
            raise StripeError("webhook_signature_invalid", "bad sig")
        return self.event


class FakeRepo:
    def __init__(self, record: SubscriptionRecord | None = None) -> None:
        self.record = record
        self.ensured: tuple | None = None
        self.applied: SubscriptionSync | None = None
        self.apply_result = True

    def get_by_tenant(self, tenant_id):
        return self.record

    def tenant_owner_email(self, tenant_id):
        return "owner@example.com"

    def ensure_customer(self, tenant_id, customer_id):
        self.ensured = (tenant_id, customer_id)

    def apply_subscription(self, sync: SubscriptionSync):
        self.applied = sync
        return self.apply_result


def _service(gateway: FakeGateway, repo: FakeRepo) -> BillingService:
    return BillingService(
        gateway=gateway,
        repository=repo,
        price_monthly=PRICE_M,
        price_annual=PRICE_A,
        trial_days=14,
        return_url="http://localhost:8000/app/settings",
    )


def test_checkout_creates_customer_then_session_with_selected_price() -> None:
    gw, repo = FakeGateway(), FakeRepo(record=None)
    tenant = uuid4()
    result = _service(gw, repo).create_checkout_session(tenant, "annual")
    assert result.url.startswith("https://checkout.stripe.test")
    assert repo.ensured == (tenant, "cus_TEST")  # new customer persisted
    assert gw.checkout_args["price_id"] == PRICE_A
    assert gw.checkout_args["trial_days"] == 14


def test_checkout_reuses_existing_customer() -> None:
    record = SubscriptionRecord(tenant_id=uuid4(), stripe_customer_id="cus_OLD")
    gw, repo = FakeGateway(), FakeRepo(record=record)
    _service(gw, repo).create_checkout_session(record.tenant_id, "monthly")
    assert gw.created_customer_for is None  # did not create a new customer
    assert gw.checkout_args["customer_id"] == "cus_OLD"
    assert gw.checkout_args["price_id"] == PRICE_M


def test_portal_requires_existing_billing_account() -> None:
    with pytest.raises(BillingServiceError, match="No billing account"):
        _service(FakeGateway(), FakeRepo(record=None)).create_portal_session(uuid4())


def test_status_reflects_entitlements() -> None:
    tenant = uuid4()
    # no record -> free only
    free = _service(FakeGateway(), FakeRepo(record=None)).get_status(tenant)
    assert free.active is False and free.status == "none"
    assert free.entitlements == ("free",)
    # active monthly -> pro
    rec = SubscriptionRecord(
        tenant_id=tenant, stripe_customer_id="cus", status="active", price_id=PRICE_M
    )
    paid = _service(FakeGateway(), FakeRepo(record=rec)).get_status(tenant)
    assert paid.active is True and paid.plan == "monthly"
    assert set(paid.entitlements) == {"free", "pro"}


def test_webhook_applies_subscription_event() -> None:
    gw, repo = FakeGateway(), FakeRepo(record=None)
    gw.event = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "customer": "cus_TEST",
                "id": "sub_1",
                "status": "active",
                "cancel_at_period_end": False,
                "current_period_end": 1750000000,
                "items": {"data": [{"price": {"id": PRICE_M}}]},
            }
        },
    }
    handled = _service(gw, repo).handle_webhook(b"{}", "sig")
    assert handled == "customer.subscription.updated"
    assert repo.applied.status == "active"
    assert repo.applied.price_id == PRICE_M


def test_webhook_ignores_unrelated_events() -> None:
    gw, repo = FakeGateway(), FakeRepo(record=None)
    gw.event = {"type": "invoice.paid", "data": {"object": {}}}
    assert _service(gw, repo).handle_webhook(b"{}", "sig") == "invoice.paid"
    assert repo.applied is None


def test_subscription_fields_reads_item_level_period_end() -> None:
    # Stripe API 2025-03-31+ drops top-level current_period_end; it's per-item.
    from app.services.billing.gateway import subscription_fields

    obj = {
        "customer": "cus_1",
        "id": "sub_1",
        "status": "trialing",
        "cancel_at_period_end": False,
        # no top-level current_period_end
        "items": {
            "data": [
                {"price": {"id": PRICE_M}, "current_period_end": 1783273343}
            ]
        },
    }
    fields = subscription_fields(obj)
    assert fields["current_period_end"] is not None
    assert fields["current_period_end"].year == 2026
    assert fields["price_id"] == PRICE_M


def test_webhook_rejects_bad_signature() -> None:
    gw, repo = FakeGateway(), FakeRepo(record=None)
    gw.event = None  # gateway raises signature error
    with pytest.raises(BillingServiceError, match="verified"):
        _service(gw, repo).handle_webhook(b"{}", "bad")
