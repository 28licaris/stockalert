"""Stripe subscription billing for customer tenants.

Stripe holds card data (hosted Checkout + Customer Portal); webhooks are the
source of truth for subscription state, which is mirrored into the identity
PostgreSQL `subscriptions` table and mapped to entitlements.
"""
