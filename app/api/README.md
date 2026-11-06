# HTTP API

Thin FastAPI adapters over services and readers. Routes validate input, obtain
dependencies, call one domain operation, and return declared Pydantic models.

## Boundaries

- `routes_*.py` owns transport concerns only; do not add business logic here.
- [`schemas/`](schemas/) owns HTTP request and response models.
- Domain behavior belongs in [`../services/`](../services/).
- Authentication dependencies shared by routes live in
  [`auth_dependencies.py`](auth_dependencies.py).
- [`auth_middleware.py`](auth_middleware.py) is the deny-by-default session
  gate (`docs/operations/deployment_dev.md` §5, C1). With `AUTH_ENABLED=true`
  every request to `/api/`, `/cockpit/`, `/ws/` or `/mcp` needs a valid session
  cookie or gets `401 {"error": "authentication_required"}` (websockets close
  `1008` before accept). Allowlisted: `/health` (liveness only — `/api/health/*`
  stays gated), `/auth/*`, `/app` + `/app/*`, and the Stripe webhook
  `/api/v1/customer/billing/webhook` (its signature is its auth). Past login it
  is also **default-deny for non-operators**: a session without `operator.access`
  may only reach the method + path-prefix rules in `CUSTOMER_RULES` (derived from
  what the SPA calls); everything else is `403 {"error": "forbidden"}` (websocket:
  close `1008`) before the route runs. `OPERATOR_RULES` is the explicit
  operator-only list, and `tests/test_route_policy.py` fails on any route in the
  real app that is in neither, so **a new route needs a rule (usually an
  operator one) in the same change**. Operator routers keep `require_operator` on
  top. With `AUTH_ENABLED=false` the gate is a no-op, so a new router under
  `/api/v1` is gated in deployment without any per-router dependency.

When adding a route, use a response model, preserve the `/api` namespace in the
surrounding router, and expose the same read behavior through MCP when agents
need it. Start at [`../main_api.py`](../main_api.py) to see router registration.

## Tests

Route and schema unit tests live in [`tests/`](tests/). Live HTTP tests that
mutate ClickHouse live in [`../../tests/integration/`](../../tests/integration/).

```bash
poetry run pytest app/api/tests -m "not integration"
```
