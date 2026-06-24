# Stream Service

Owns the live Schwab subscription set + the `stream_universe` CH table
(the operator's "things we stream into ClickHouse 24/7" list).

Implements the locked sticky-universe model defined in
[`docs/frontend_api_contracts.md §10.1`](../../../docs/frontend_api_contracts.md).

## What it owns

| Concern | Where |
|---|---|
| `stream_universe` CH table — CRUD + `is_active` flag | `service.py` |
| Schwab CHART_EQUITY subscriptions | `service.py` (`_apply_subscription_diff`) |
| Live bar → CH `ohlcv_1m` forwarding (source tag `{provider}-stream`) | `service.py` (`_on_bar`) |
| Auto-extend hook called by `WatchlistService.add_members` | `service.py` (`ensure_streaming`) |
| Backfill warmup on `add` (silver→CH + Schwab tip-fill) | `service.py` (`_enqueue_warmup`) |

## Public contract

See [`contract.py`](contract.py). The only methods other services /
routes should call:

- `start()` / `stop()` — lifecycle, wired in `app/main_api.py` lifespan.
- `list_universe()` / `is_streaming(sym)` — reads.
- `add(sym)` / `remove(sym)` / `import_bulk(syms)` — operator-driven
  CRUD. **`remove` is the ONLY path that strips a symbol from the
  live stream.**
- `ensure_streaming(syms, source=)` — called by `watchlist_service.add_members`
  to auto-promote non-universe symbols into the stream.
- `status()` — drives the cockpit `/app/status` tile.

## Why watchlists call us (not the reverse)

Pre-FE-CONTRACTS-4, `watchlist_service` owned subscriptions via a
per-symbol refcount: adding a symbol to ANY watchlist subscribed it,
and removing it from the LAST watchlist unsubscribed it. The locked
sticky model inverts that:

- The stream universe is the source of truth.
- Watchlist add → if symbol ∉ universe, `ensure_streaming` adds it.
- Watchlist remove → no stream effect. Universe stays sticky.
- Only `StreamService.remove` (the dedicated stream-universe page in
  the cockpit) strips a symbol from the stream.

This guarantees that re-organizing watchlists never loses streaming
coverage.

## Source tag contract

Every bar emitted via `_on_bar` carries `source = "{provider}-stream"`
(e.g. `schwab-stream`). `live_lake_writer` (TA-5.7) reads
`ohlcv_1m WHERE source = "{provider}-stream"` to flush live-stream
rows into bronze. **Do not change this tag without updating
live_lake_writer's filter.** Operator overrides via `DATA_SOURCE_TAG`
must match on both sides.

## How to test

```bash
# Unit tests use fake provider and repository boundaries:
poetry run pytest app/services/stream/tests -m "not integration"

# Manual: subscribe count + recent bars
curl http://localhost:8000/api/v1/stream/status

# CH query — distinct streamed symbols in the last 10 min
curl "http://localhost:8123/?database=stocks" \
  --data-binary "SELECT uniqExact(symbol) FROM ohlcv_1m \
                 WHERE source='schwab-stream' \
                 AND timestamp > now() - INTERVAL 10 MINUTE"
```

## Module shape (per `docs/standards/service_modules.md`)

```
stream/
├── __init__.py    Re-exports the singleton + schemas
├── schemas.py     Pydantic DTOs — only file other services import
├── contract.py    Protocol — public interface
├── service.py     Implementation — NEVER imported across services
├── README.md      This file
└── tests/         Unit tests owned by this service
```

## Migration notes

- The CH table was renamed from `seed_universe` → `stream_universe` in
  FE-CONTRACTS-4 final cutover. `app/db/init.py::_migrate_seed_to_stream_universe`
  performs the one-shot rename on first startup post-deploy.
- `app/services/seed/` is now a thin re-export shim pointing here.
  Both `seed_service` and `stream_service` are the same singleton.
  Direct imports of `seed_service` should be migrated to `stream_service`.
- Followups (not in this PR):
  - Retire `app/data/seed_universe.py::SEED_SYMBOLS` Python constant.
  - Rewire `app/services/universe/active_universe.py::get_active_universe`
    to read from the CH table instead of `SEED_SYMBOLS ∪ watchlists`.
