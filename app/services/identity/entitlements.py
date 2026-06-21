"""Map Stripe billing state to product entitlements.

This is the single seam where plans/tiers are defined. Adding or renaming a tier
is a config change here (plus creating the price in Stripe) — never a schema or
control-flow change. v1 is deliberately binary: any active/trialing subscription
grants the ``pro`` entitlement; everything else is free.
"""
from __future__ import annotations

# Stripe subscription statuses that grant paid entitlements. ``past_due`` is
# included so a customer keeps access during Stripe's dunning/retry window.
_ENTITLED_STATUSES = frozenset({"active", "trialing", "past_due"})

# Entitlements granted to every authenticated customer, paid or not.
FREE_ENTITLEMENTS: frozenset[str] = frozenset({"free"})

# price_id -> entitlements unlocked by that price. Populated once the Stripe
# Products/Prices exist (test-mode price IDs go here, or later via config). An
# unmapped but active price falls back to DEFAULT_PAID_ENTITLEMENTS so a new
# price never silently grants nothing.
PRICE_ENTITLEMENTS: dict[str, frozenset[str]] = {}

# Fallback for an active subscription whose price_id is not in the map above.
DEFAULT_PAID_ENTITLEMENTS: frozenset[str] = frozenset({"pro"})


def entitlements_for(status: str | None, price_id: str | None) -> frozenset[str]:
    """Return the entitlement set for a tenant's current billing state.

    Free entitlements are always granted; paid entitlements are added only when
    the subscription status is active/trialing/past_due.
    """
    if status not in _ENTITLED_STATUSES:
        return FREE_ENTITLEMENTS
    paid = PRICE_ENTITLEMENTS.get(price_id or "", DEFAULT_PAID_ENTITLEMENTS)
    return FREE_ENTITLEMENTS | paid
