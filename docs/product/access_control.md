# Access control — admin / customer split

Status: **living policy** (2026-07-01). Records who sees each surface and what
a customer may do on it. The cockpit began life as a single-operator tool; this
doc is the target split as it becomes a multi-tenant SaaS.

## Tiers

| Tier | Who | How identified |
|------|-----|----------------|
| **Public** | Logged-out visitors | no session |
| **Customer** | Authenticated tenant user (no operator rights) | session Principal, `Role.OWNER`/`member`/… |
| **Operator (admin)** | Platform owner / staff | `operator.access` permission |

`operator.access` is granted by **either**:
- the user's email is in `ADMIN_EMAILS` (comma-separated `.env` allowlist) — bootstraps the founder-admin with no DB write, survives identity-DB rebuilds; **or**
- the membership role is `Role.ADMIN` (dynamic grants later).

Derived in `app/services/identity/permissions.py::permissions_for`. When
`AUTH_ENABLED=false` (local dev cockpit) the local operator is treated as admin.

## Enforcement (defence in depth)

1. **One default-deny gate, in front of every router.**
   `SessionGateMiddleware` (`app/api/auth_middleware.py`, active while
   `AUTH_ENABLED=true`) requires a session for `/api/`, `/cockpit/`, `/ws/` and
   `/mcp`, then lets a **non-operator** through only for the method + path-prefix
   rules in `CUSTOMER_RULES`. Everything else is operator-only (`403`; websocket
   close `1008`). A route nobody classified is therefore operator-only, not open.
   `OPERATOR_RULES` is the explicit operator-only list and
   `app/api/tests/test_route_policy.py` **fails on any route of the real app that
   is in neither list** — adding an endpoint forces a decision in the same change.
2. **Router-level `require_operator`** (`app/api/auth_dependencies.py`) stays on
   the admin routers (`routes_jobs`, `routes_clickhouse`, `routes_health`,
   `routes_ppl`, admin auth) as a second lock; a test asserts none of them can be
   opened by a customer rule. Add new admin APIs with
   `dependencies=[Depends(require_operator)]` *and* an `OPERATOR_RULES` entry.
3. **Assistant.** Conversations are scoped to the caller's tenant (session
   principal; `AUTH_ENABLED=false` keeps the fixed local dev principal). Non-operators
   get only `CUSTOMER_TOOLS` (`app/services/assistant/policy.py`), enforced in the
   prompt and again at tool execution. Every registered MCP tool must be classified
   (test).
4. **Frontend hides + guards.** Nav items carry `adminOnly`; admin routes are
   wrapped in `AdminOnly`; the Library "Owner view" and the Sectors add/remove
   theme controls render for operators only. **UI hiding is convenience, not
   security — the API is the boundary.**

### Customer-allowed API (summary; the code is authoritative)

Read: bars, signals, instruments search/lookup, market banner, calendar, news,
economic, options hot tier + GEX, Elliott Wave, sector rotation + themes,
research rankings, paper status/export, library. Compute: `POST indicators/chart-data`,
backtest catalog/run/runs. Per-user: `/customer/*`, `/my/watchlists*`.
Assistant: `/cockpit/assistant/*`.

Operator-only: stream, backfill, jobs, ClickHouse console, health, lake browser,
monitors, the journal (the operator's Schwab account), shared `/watchlists` and
`/watchlist`, strategies (all), theme create/delete, screener, adjusted, corp-actions,
movers, PPL, `indicators/series`, non-latest `options/contracts`, `/ws`, `/mcp`.

## Per-page policy

Legend: ✅ = allowed · read = view only · CRUD(own) = create/read/update/delete
their own tenant-scoped rows · 🔒 = subscription-entitlement gated (future).

### Customer-facing (product)

| Page | Route | Customer may | Enforcement now | TODO |
|------|-------|--------------|-----------------|------|
| Charts | `/charts` | read any symbol's bars/indicators | session + customer rule | — |
| Elliott Wave | `/ewt` | read wave labels | session + customer rule | — |
| Watchlists | `/watchlists` | CRUD(own) | `/my/watchlists` per-user; shared `/watchlists` operator-only | — |
| Options (GEX) | `/options` | read | session + customer rule | 🔒 premium tier |
| Calendar | `/calendar` | read | session + customer rule | — |
| News | `/news` | read | session + customer rule | — |
| Economic | `/economic` | read | session + customer rule | — |
| Sectors (RRG) | `/sectors` | read; theme add/remove is operator-only | read: customer rule; theme writes operator-only | — |
| Screener | `/screener` (flag off) | read + run screens | operator-only until the page ships | 🔒 |
| Backtest | `/backtest` | run backtests | session + customer rule (**run registry is shared**) | 🔒, tenant-scope runs |
| Strategy Library | `/library` | read redacted cards + alerts; owner view operator-only | `/library` customer rule; `/strategies*` operator-only | 🔒 subscriber view |
| Paper Trading | `/paper` | read forward track record | session + customer rule | — |
| Journal | `/journal` (flag off) | CRUD(own) | operator-only (it is the operator's Schwab account) | tenant-scope before opening |
| Monitors | `/monitors` (flag off) | CRUD(own) alerts | operator-only | tenant-scope before opening |
| Settings | `/settings` | edit **own** account only | `/customer/*` (Principal-scoped) | — |

### Operator-only (admin)

| Page | Route | Purpose | Enforcement now |
|------|-------|---------|-----------------|
| System Health | `/` (index) | services + scheduled-jobs registry | `AdminOnly` + `/health/services`, `/jobs` gated |
| ClickHouse console | `/clickhouse` | ad-hoc SQL | `AdminOnly` + `/clickhouse` gated |
| Stream | `/stream` | manage the provider ingest universe (subscribe/unsubscribe Schwab) | `AdminOnly` + API operator-only |
| Lake | `/lake` (flag off) | Iceberg lake browser | flag off; mark `adminOnly` when enabled |
| Coverage | `/coverage` (flag off) | per-symbol data coverage | flag off; `adminOnly` |
| Indicators (debug) | `/indicators` (flag off) | indicator math inspector | flag off; `adminOnly` |
| MCP | `/mcp` (flag off) | agent tooling surface | flag off; `adminOnly` |

**Data-provider identity (Schwab / Polygon / Alpaca) and infra names
(ClickHouse / Iceberg) must never appear in customer-facing copy.** The bottom
StatusBar, Stream, and System Health carry these and are operator-only; the
Options page copy was scrubbed of provider/infra names.

## Known gaps / follow-ups

1. **Stream API.** Operator-only since the role policy (the per-user watchlist
   feature extends the stream universe server-side, not through `/stream`).
2. **Subscription-entitlement gating (🔒).** Premium pages (Options, Backtest,
   Library, Screener) should additionally check `entitlements` beyond mere
   authentication. Entitlements already exist on the Principal
   (`entitlements_for`); wire per-page checks when billing activates.
3. **Tenant-scoping.** Watchlists (`/my/watchlists`), billing/sessions and the
   assistant are per-user. **Open gap: backtest runs** — the `agent_runs` registry is
   shared, so `GET /backtest/runs[/{id}]` lets any customer see every run's config
   (including the operator's). Journal and Monitors are operator-only until they are
   tenant-scoped. Chat answers cached in `.cache/assistant_responses.sqlite` are shared
   across users but only for text-only turns.
4. **Public marketing site + customer dashboard.** Logged-out visitors need a
   marketing landing; authenticated customers need a home dashboard (today
   non-admins are redirected to `/charts` as an interim home). Separate build.
