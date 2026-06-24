# Billing

Stripe subscription orchestration for customer tenants. Stripe remains the
payment-data and webhook source of truth; this service maps subscription state
to identity records and entitlements.

| File | Purpose |
|---|---|
| `schemas.py` | Billing DTOs |
| `gateway.py` | Stripe API translation |
| `repository.py` | Subscription persistence |
| `service.py` | Billing workflows and error contract |

Authentication and authorization belong to [`../identity/`](../identity/).
Routes belong to [`../../api/`](../../api/). Unit tests live in [`tests/`](tests/)
and must not call Stripe or a live database.
