# Stream Service

Owns the live Schwab subscription set + the `stream_universe` CH table
(the operator's "things we stream into ClickHouse 24/7" list).

Implements the locked sticky-universe model defined in
[`docs/product/api_contracts.md §10.1`](../../../docs/product/api_contracts.md).

## What it owns

| Concern | Where |
|---|---|
| `stream_universe` CH table — CRUD + `is_active` flag | `service.py` |
| Schwab CHART_EQUITY subscriptions | `service.py` (`_apply_subscription_diff`) |
| Live bar → CH `ohlcv_1m` forwarding (source tag `{provider}-stream`) | `service.py` (`_on_bar`) |
| Auto-extend hook called by `WatchlistService.add_members` | `service.py` (`ensure_streaming`) |
| Backfill warmup on `add` (silver→CH + Schwab tip-fill) | `service.py` (`_enqueue_warmup`) |
| Periodic re-sync of the live set from `stream_universe` ∪ `futures_universe` | `service.py` (`_universe_resync_loop` / `resync_universe`) |

## Public contract

See [`contract.py`](contract.py). The only methods other services /
routes should call:

- `start()` / `stop()` — lifecycle, wired in `app/main_api.py` lifespan.
- `resync_universe()` — one re-sync cycle (see *Lifecycle*). The loop
  calls it on a timer; the `stream_universe_resync` job's ▶ button calls
  it on demand.
- `list_universe()` / `is_streaming(sym)` — reads.
- `add(sym)` / `remove(sym)` / `import_bulk(syms)` — operator-driven
  CRUD. **`remove` is the ONLY path that strips a symbol from the
  live stream.**
- `ensure_streaming(syms, source=)` — called by `watchlist_service.add_members`
  to auto-promote non-universe symbols into the stream.
- `status()` — drives the cockpit `/app/status` tile.

## Lifecycle

| Step | What happens |
|---|---|
| `start()` | Captures the main event loop, seeds `futures_universe` if empty, reads `stream_universe` ∪ `futures_universe` and subscribes everything. Then starts the re-sync loop (below). |
| `add()` / `remove()` / `add_futures()` / `remove_futures()` | Write the CH row **and** apply the subscribe/unsubscribe in-process, immediately. |
| re-sync loop | Every `STREAM_UNIVERSE_RESYNC_SECONDS` (default 60; `0` disables) `resync_universe()` re-reads the active universe and applies the diff against `_subscribed` with the same `_apply_subscription_diff` the CRUD paths use. |
| `stop()` | Cancels the re-sync loop, unsubscribes everything, stops the provider stream. |

### Why the re-sync loop exists (deployment spec C2)

In the AWS dev deployment
([`docs/operations/deployment_dev.md`](../../../docs/operations/deployment_dev.md)
§3) the app runs as two containers from one image: `api` (request path,
`STREAM_PROVIDER=""`) and `worker` (the streamer). A customer's watchlist
add runs in `api`: `ensure_streaming()` writes the `stream_universe` row,
and the in-process subscribe is a logged no-op because `api` has no
provider. Before C2 the `worker` read the table only in `start()`, so the
symbol would not stream until the worker restarted. The loop closes that
gap: the CH table is the source of truth, and every process that streams
converges on it within one cycle. Acceptance test: spec §10.5.

Rules the cycle follows:

- **Only runs where a provider is configured.** `start()` spawns the loop
  iff `STREAM_UNIVERSE_RESYNC_SECONDS > 0` and
  `settings.effective_stream_provider` is non-empty. Each branch logs, so
  "off by config" is distinguishable from "never started".
- **Every outcome is logged, including zero** —
  `Stream resync: subscribed=+N -M universe=T`. A no-op cycle returns
  `{"subscribed": 0, "unsubscribed": 0}`; `audit_run(frequent=True)`
  collapses those to one `ingestion_runs` heartbeat per UTC day, so the
  job shows on the Health page without flooding the runs log. Cycles
  that changed something or errored always write a row.
- **A failing cycle never ends the loop.** The exception is logged with
  its traceback (`Stream resync: cycle N failed`) and the next cycle runs.
  `_subscribed` is left untouched on failure.
- **Both tables or nothing.** The cycle reads `futures_universe` in strict
  mode: an unreadable table raises instead of being treated as empty,
  because an empty set applied as a diff would unsubscribe every live
  futures root. (`start()` keeps the lenient read — nothing is subscribed
  yet, and futures are optional.)
- **Lock discipline.** The read and the diff happen under `_lock`, so an
  `add()` racing the cycle cannot subscribe a symbol the cycle's read
  predates and then have the diff unsubscribe it. The lock is taken with a
  non-blocking poll (not a blocking `with`): route threads inside `add()`
  hold it while marshalling their provider call onto the main loop, so
  blocking the loop on the lock would deadlock them. If the lock stays busy
  for `RESYNC_LOCK_WAIT_SECONDS` the cycle reports `skipped: lock_busy` and
  the next one retries.

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
# Unit tests use fake provider and repository boundaries; the re-sync tests
# inject a 20 ms interval + a recording audit stub (no ClickHouse):
poetry run pytest app/services/stream/tests -m "not integration"

# Re-sync end-to-end (spec §10.5): with the worker streaming, insert a row
# from another process and watch the worker log within one cycle:
curl -X POST http://localhost:8000/api/v1/stream -H 'content-type: application/json' -d '{"symbol":"NVDA"}'
#   worker log → "Stream resync: subscribed=+1 -0 universe=N added=['NVDA']"
curl http://localhost:8000/api/v1/jobs | jq '.jobs[] | select(.name=="stream_universe_resync")'

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

## Historical migration

- The CH table was renamed from `seed_universe` → `stream_universe` in
  FE-CONTRACTS-4 final cutover. `app/db/init.py::_migrate_seed_to_stream_universe`
  performs the one-shot rename on first startup post-deploy.
- The legacy seed service, `/api/v1/seed` routes, and static equity seed list
  were retired after `stream_universe` became the sole runtime source of truth.
- Empty tables remain empty until an operator explicitly adds or imports
  symbols through `/api/v1/stream`; reads never repopulate the table.
