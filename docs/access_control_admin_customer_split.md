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

1. **Backend is the boundary.** `require_operator` (`app/api/auth_dependencies.py`)
   gates admin routers: `routes_jobs`, `routes_clickhouse`, `routes_health`
   (`/health/services`). Public liveness `/health` stays open. Add new admin
   APIs by mounting them with `dependencies=[Depends(require_operator)]`.
2. **Frontend hides + guards.** Nav items carry `adminOnly` (filtered in
   `Sidebar` unless `operator.access`); admin routes are wrapped in
   `AdminOnly` (`frontend/src/auth/AdminOnly.tsx`), which redirects non-admins
   to `/charts`. The bottom subsystem-health `StatusBar` renders only for
   operators. **UI hiding is convenience, not security — always gate the API.**

## Per-page policy

Legend: ✅ = allowed · read = view only · CRUD(own) = create/read/update/delete
their own tenant-scoped rows · 🔒 = subscription-entitlement gated (future).

### Customer-facing (product)

| Page | Route | Customer may | Enforcement now | TODO |
|------|-------|--------------|-----------------|------|
| Charts | `/charts` | read any symbol's bars/indicators | open | — |
| Elliott Wave | `/ewt` | read wave labels | open | — |
| Watchlists | `/watchlists` | CRUD(own) | open | tenant-scope on API |
| Options (GEX) | `/options` | read | open | 🔒 premium tier |
| Calendar | `/calendar` | read | open | — |
| News | `/news` | read | open | — |
| Economic | `/economic` | read | open | — |
| Sectors (RRG) | `/sectors` | read | open | — |
| Screener | `/screener` (flag off) | read + run screens | open | 🔒 |
| Backtest | `/backtest` | run backtests (own runs) | open | 🔒, tenant-scope runs |
| Strategy Library | `/library` | read + subscribe | open | 🔒 subscriber view |
| Paper Trading | `/paper` | read forward track record | open | — |
| Journal | `/journal` (flag off) | CRUD(own) | open | tenant-scope |
| Monitors | `/monitors` (flag off) | CRUD(own) alerts | open | tenant-scope |
| Settings | `/settings` | edit **own** account only | open | scope to self |

### Operator-only (admin)

| Page | Route | Purpose | Enforcement now |
|------|-------|---------|-----------------|
| System Health | `/` (index) | services + scheduled-jobs registry | `AdminOnly` + `/health/services`, `/jobs` gated |
| ClickHouse console | `/clickhouse` | ad-hoc SQL | `AdminOnly` + `/clickhouse` gated |
| Stream | `/stream` | manage the provider ingest universe (subscribe/unsubscribe Schwab) | `AdminOnly` (⚠ API not yet gated) |
| Lake | `/lake` (flag off) | Iceberg lake browser | flag off; mark `adminOnly` when enabled |
| Coverage | `/coverage` (flag off) | per-symbol data coverage | flag off; `adminOnly` |
| Indicators (debug) | `/indicators` (flag off) | indicator math inspector | flag off; `adminOnly` |
| MCP | `/mcp` (flag off) | agent tooling surface | flag off; `adminOnly` |

**Data-provider identity (Schwab / Polygon / Alpaca) and infra names
(ClickHouse / Iceberg) must never appear in customer-facing copy.** The bottom
StatusBar, Stream, and System Health carry these and are operator-only; the
Options page copy was scrubbed of provider/infra names.

## Known gaps / follow-ups

1. **Stream API not gated.** Only the UI is hidden. `routes_stream` mutations
   (add/remove universe symbol) should be operator-gated — but the watchlist
   feature extends the stream universe server-side, so gate the *mutation*
   endpoints selectively, not the whole router.
2. **Subscription-entitlement gating (🔒).** Premium pages (Options, Backtest,
   Library, Screener) should additionally check `entitlements` beyond mere
   authentication. Entitlements already exist on the Principal
   (`entitlements_for`); wire per-page checks when billing activates.
3. **Tenant-scoping.** Customer CRUD pages (Watchlists, Journal, Monitors,
   Backtest runs, Settings) must scope reads/writes to the caller's tenant.
4. **Public marketing site + customer dashboard.** Logged-out visitors need a
   marketing landing; authenticated customers need a home dashboard (today
   non-admins are redirected to `/charts` as an interim home). Separate build.
