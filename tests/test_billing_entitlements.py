"""Stripe billing-state -> entitlement mapping (the plan/tier seam)."""
from __future__ import annotations

from app.services.identity import entitlements as ent


def test_active_and_trialing_and_past_due_grant_paid_entitlements() -> None:
    for status in ("active", "trialing", "past_due"):
        result = ent.entitlements_for(status, None)
        assert "free" in result
        assert "pro" in result


def test_inactive_statuses_grant_only_free() -> None:
    for status in ("canceled", "incomplete", "unpaid", "none", None):
        assert ent.entitlements_for(status, None) == ent.FREE_ENTITLEMENTS


def test_known_price_overrides_default_paid_set() -> None:
    ent.PRICE_ENTITLEMENTS["price_team_test"] = frozenset({"pro", "team"})
    try:
        result = ent.entitlements_for("active", "price_team_test")
        assert result == frozenset({"free", "pro", "team"})
    finally:
        del ent.PRICE_ENTITLEMENTS["price_team_test"]


def test_unmapped_active_price_falls_back_to_default_paid() -> None:
    # A new price not yet in the map must still grant paid access, never nothing.
    result = ent.entitlements_for("active", "price_brand_new")
    assert result == ent.FREE_ENTITLEMENTS | ent.DEFAULT_PAID_ENTITLEMENTS
