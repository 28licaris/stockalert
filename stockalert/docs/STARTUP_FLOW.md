# Startup Flow

How the FastAPI process boots, what each step does, and how to verify
the right things are happening. Reference doc — updated when the
startup sequence changes.

For the big-picture architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).
For the medallion lake design, see [data_platform_plan.md](data_platform_plan.md).

## The two ingestion tiers

The system has **two independent ingestion tiers** that share no
runtime dependencies. Either can be off without affecting the other.

| Tier | Purpose | Latency | Writers | Readers |
|---|---|---|---|---|
| **Hot (ClickHouse)** | Live alerts, UI charts, divergence detection | seconds | `streamer` (WS bars), `backfill_service` (REST → CH) | FastAPI routes, alert engine, dashboard |
| **Cold (Iceberg bronze)** | ML training, backtests, historical analysis | T+1 day | `nightly_polygon_refresh`, `nightly_schwab_refresh` | Athena, PyIceberg, DuckDB |

The cold tier does NOT depend on `DATA_PROVIDER`. Its writers pull from
Polygon flat files or Schwab REST regardless of what's streaming live.

## Startup sequence (lifespan handler)

[app/main_api.py:46-175](../app/main_api.py) runs this in order on
every FastAPI startup. Total wall time: ~3 seconds.

```
1.  init_schema()                          [HOT]   CH DDL, idempotent
2.  migrate_default_watchlist()            [OPS]   one-time helper, no-op after first run
3.  batcher.start()                        [HOT]   OHLCV batch writer (500 rows / 5s flush)
4.  backfill_service.start()               [HOT]   REST → CH ingest, gap-fill jobs
5.  watchlist_service.start()              [HOT]   ⚠ tries to init stream provider; soft-fails if no creds
6.  set_symbol_provider() + gap sweeper    [HOT]   armed daily 06:00 UTC, 7d window
7.  _initial_gap_sweep_after_warmup()      [HOT]   one-shot 30s after start, repairs holes from downtime
8.  journal_sync_service.start()           [OPS]   if JOURNAL_ENABLED + Schwab creds → balances+trades every 5min
9.  nightly_polygon_refresh                [COLD]  if POLYGON_NIGHTLY_ENABLED + STOCK_LAKE_BUCKET → bronze.polygon_minute
10. nightly_schwab_refresh                 [COLD]  if SCHWAB_NIGHTLY_ENABLED  + STOCK_LAKE_BUCKET → bronze.schwab_minute
11. install broadcast_signal helper        [API]   WebSocket fan-out
12. yield → serve HTTP/WS
```

## Gates and what each one controls

| Gate | Where | What it enables |
|---|---|---|
| `DATA_PROVIDER` | `.env` | Default for live streaming + history REST. `STREAM_PROVIDER` / `HISTORY_PROVIDER` override per role. |
| Provider credentials (e.g. `ALPACA_API_KEY`, `SCHWAB_REFRESH_TOKEN`) | `.env` | Whether `watchlist_service` can actually subscribe to bars. Missing creds → soft-fail (logged ERROR + WARNING; app continues). |
| `JOURNAL_ENABLED` + Schwab creds | `.env` | Whether `journal_sync_service` polls Schwab account balances + trades into CH. |
| `POLYGON_NIGHTLY_ENABLED` + `STOCK_LAKE_BUCKET` | `.env` | Whether the 07:00 UTC Polygon → bronze loop runs. |
| `SCHWAB_NIGHTLY_ENABLED` + `STOCK_LAKE_BUCKET` + Schwab creds | `.env` | Whether the 22:00 UTC Schwab → bronze loop runs. |

Note the symmetry: the two nightly jobs have identical gate shape and
behavior. Neither depends on `DATA_PROVIDER` — they have their own
provider-specific credentials and run independently.

## How to verify everything's running

After `Application startup complete`, scan the log for these lines.
Each one corresponds to one subsystem actually being live:

```
✅ ClickHouse schema ready                                # step 1
✅ OHLCV batch writer started                             # step 3
✅ Backfill service ready                                 # step 4
✅ Watchlist service started (provider=X, symbols=N)      # step 5 — if no ERROR above, streaming is OK
✅ Backfill gap sweeper armed (daily at 06:00 UTC)        # step 6
✅ Journal sync started (every 5min: balances + trades)   # step 8 — only if Schwab creds + JOURNAL_ENABLED
nightly_polygon_refresh: background loop started ...      # step 9 — only if POLYGON_NIGHTLY_ENABLED
nightly_schwab_refresh: background loop started  ...      # step 10 — only if SCHWAB_NIGHTLY_ENABLED
✅ Application startup complete
```

If a line is missing, the corresponding subsystem is OFF — usually
because of an env flag or missing credentials. The map above tells
you which gate to check.

## Cadence reference

| Job | Schedule | Direction |
|---|---|---|
| `streamer` (live WS) | continuous | provider WS → CH `ohlcv_1m` |
| `batcher` | 500 rows or 5s, whichever first | in-memory queue → CH |
| `backfill_service` quick path | on watchlist change + every 15 min | provider REST → CH |
| `backfill_service` deep path | on demand via API | provider REST → CH |
| Backfill gap sweeper | daily 06:00 UTC | scans for CH holes, enqueues fixes |
| `journal_sync_service` | every 5 minutes | Schwab API → CH `account_snapshots`, `trades` |
| `nightly_polygon_refresh` | daily 07:00 UTC (midnight Arizona) | Polygon flat file → `bronze.polygon_minute` |
| `nightly_schwab_refresh` | daily 22:00 UTC (3 PM Arizona) | Schwab pricehistory → `bronze.schwab_minute` |

The two nightly jobs intentionally run at very different hours:
- 07:00 UTC for Polygon — well after Polygon's daily flat file is
  published (typically by 04:00 UTC).
- 22:00 UTC for Schwab — ~30 minutes after NYSE 4 PM ET close, so
  the day's pricehistory is complete.

## Common startup issues

### Watchlist logs ERROR + WARNING then "started"

```
ERROR Watchlist: could not initialize stream provider: You must supply a method of authentication
WARNING Watchlist: provider unavailable, skipping subscribe=[...]
INFO ✅ Watchlist service started (provider=X, symbols=11, streaming=11)
```

The "streaming=11" is misleading — symbols are subscribed in the
watchlist repo but no provider is feeding them bars. Fix by setting
the right credentials for `DATA_PROVIDER`, or change `DATA_PROVIDER`
to one whose creds you have.

### Both nightly loops missing from the log

Means both `*_NIGHTLY_ENABLED` flags are `false`. The cold tier won't
receive new data. Flip the flags in `.env`, restart.

### Backfill spam at startup

Many `Backfill quick X: skipped (NNN bars, ratio=...)` lines is
normal — the gap sweeper checks each watchlist symbol once at startup
and reports "already have enough" for symbols with sufficient
historical data. Not an error.

## Shutdown sequence

Cancels all background tasks in reverse order. From
[main_api.py:177-220](../app/main_api.py):

1. Cancel nightly_polygon_refresh task
2. Cancel nightly_schwab_refresh task
3. `monitor_manager.stop_all()`
4. `watchlist_service.stop()`
5. `backfill_service.stop()`
6. `journal_sync_service.stop()` (if it was started)
7. `get_bar_batcher().stop()` (flushes pending writes)
8. `close_client()` (CH connection)

## Recommended improvements (not yet implemented)

Tracked in [BUILD_JOURNAL.md](BUILD_JOURNAL.md) deferred items:

1. **`/health/services` endpoint** — returns JSON status of every
   subsystem, grouped by tier. Removes the need to parse logs.
2. **Tier-grouped startup logs** — three section headers (`HOT TIER`,
   `COLD TIER`, `OPS`) so a single glance tells you what's healthy.
3. **Cleaner soft-fail for missing creds** — single WARNING line, not
   ERROR + WARNING + ✅.
4. **Extend initial gap sweep to bronze** — when downtime crosses a
   nightly window, the catchup loop should also nudge a bronze
   backfill for missed dates.
