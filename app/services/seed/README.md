# `app/services/seed/` — Seed Universe Service

CRUD over the ClickHouse `seed_universe` table — the operator's
explicit "permanently part of the streaming universe" set.

See [docs/frontend_api_contracts.md §10.1](../../../docs/frontend_api_contracts.md)
for the sticky-universe model.

## Files

```
seed/
├── __init__.py          re-exports seed_service singleton
├── seed_service.py      list / add / remove / import_bulk / bootstrap
└── README.md            this file
```

## Contract

| Method | Returns | Side effects |
|---|---|---|
| `list_seed(owner_id=)` | `list[dict]` of active entries | none |
| `add(symbol, ...)` | mutation result dict | upsert `seed_universe` row + `WatchlistService.add_members("default", [sym])` (subscribes Schwab stream + triggers backfill) |
| `remove(symbol, ...)` | mutation result dict | mark row inactive + `WatchlistService.remove_members("default", [sym])` (decrements refcount; symbols held by other watchlists keep streaming) |
| `import_bulk(symbols, ...)` | mutation result dict | calls `add` per symbol |
| `is_empty(owner_id=)` | `bool` | none |
| `bootstrap_if_empty(owner_id=)` | `(did, count)` | bulk-insert from `SEED_SYMBOLS ∪ default-watchlist` if the table is empty. Does NOT trigger subscribe side-effects (those symbols are already in the watchlist machinery). |

## Why a CH table when symbols are already in `SEED_SYMBOLS` env + watchlists?

- **Editable from the cockpit.** Env vars + Python tuples require a
  restart; a CH table allows the operator to add/remove symbols at
  runtime.
- **Audit log.** `added_at` + `added_by` columns give a permanent record
  of who promoted what and when. The legacy env list is opaque.
- **Multi-tenant ready.** `owner_id` column is the SaaS-readiness seam;
  per-tenant streaming sets land as a column filter, no schema change.
- **Sticky-universe enforcement.** This is the *only* place where the
  operator explicitly designates "this symbol streams forever." The
  refcounted watchlist machinery stays correct because seed
  mutations call into it.

## Caching / threading

Stateless. The underlying CH client manages its own pool via
`app.db.client.get_client()`.

## Read flow (cockpit's `GET /api/v1/seed`)

1. `seed_service.bootstrap_if_empty()` — no-op on warm calls; on the
   first call after the CH table is created, populates it from
   `SEED_SYMBOLS ∪ default-watchlist-members` so the page isn't empty
   on cold start.
2. `seed_service.list_seed()` returns the active rows.
3. Route wraps them in the `SeedUniverseResponse` Pydantic envelope.

## What's deliberately NOT here

- **Per-tenant quota / cost-control on seed size.** Future SaaS phase.
- **Provider-aware asset-type derivation.** Today `asset_type` is empty
  unless the caller supplies it. A future enrichment pass can hydrate
  the column from an instruments lookup.
- **Auto-removal of stale entries.** If a symbol stops trading
  (delisting, ticker change), we don't auto-clean. Manual operator
  action only.
- **`active_universe.get_active_universe()` doesn't read the CH table yet.**
  The seed table is additive — the existing universe resolution
  (`SEED_SYMBOLS ∪ active-watchlists`) stays unchanged. Wiring it
  is a separate phase once we're confident the CH table is the
  durable source of truth.
