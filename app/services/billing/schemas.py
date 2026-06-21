"""Pydantic DTOs crossing the billing service boundary."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# Plans the customer may select at checkout. Maps to a configured Stripe price.
BillingPlan = str  # "monthly" | "annual"


class CheckoutRequest(BaseModel):
    plan: str = Field(pattern=r"^(monthly|annual)$")


class CheckoutSessionResponse(BaseModel):
    url: str


class PortalSessionResponse(BaseModel):
    url: str


class SubscriptionStatusResponse(BaseModel):
    """Current billing state for the authenticated tenant."""

    status: str  # Stripe status, or "none" when no subscription exists
    active: bool  # whether paid entitlements are currently granted
    plan: str | None = None  # "monthly" | "annual" | None
    price_id: str | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    entitlements: tuple[str, ...] = ()


class SubscriptionRecord(BaseModel):
    """Internal persisted billing state for a tenant."""

    tenant_id: UUID
    stripe_customer_id: str
    stripe_subscription_id: str | None = None
    status: str = "none"
    price_id: str | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False


class SubscriptionSync(BaseModel):
    """Fields applied from a Stripe subscription object via webhook."""

    stripe_customer_id: str
    stripe_subscription_id: str
    status: str
    price_id: str | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
