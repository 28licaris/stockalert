# Startup Flow

How the FastAPI process boots, what each step does, and how to verify
the right things are happening. Reference doc — updated when the
startup sequence changes.

Architecture context: [`../architecture/README.md`](../architecture/README.md).
Lake design: [`../architecture/lake_schema.md`](../architecture/lake_schema.md).
Operator procedures: [`runbook.md`](runbook.md).

## The two ingestion tiers

The system has **two independent ingestion tiers** that share no
runtime dependencies. Either can be off without affecting the other.

| Tier | Purpose | Latency | Writers | Readers |
|---|---|---|---|---|
| **Hot (ClickHouse)** | Live alerts, UI charts, divergence detection | seconds | `streamer` (WS bars), `backfill_service` (REST → CH) | FastAPI routes, alert engine, dashboard |
| **Cold (Iceberg lake)** | ML training, backtests, historical analysis | T+1 day | the nightly refresh loops below, `live_lake_writer` | `read_arrow` / readers, Athena, PyIceberg |

The cold tier does NOT depend on `DATA_PROVIDER`. Its writers pull from
Polygon flat files or Schwab REST regardless of what's streaming live.

## Startup sequence (lifespan handler)

`lifespan()` in [app/main_api.py](../../app/main_api.py) runs this in order on
every start. Total wall time: ~3 seconds. Every non-foundation subsystem is
wrapped in `_safe_start()`, so one failing subsystem logs and is skipped rather
than aborting boot.

```
FOUNDATION (failure here aborts startup)
 1. init_schema()                      [HOT]   CH DDL, idempotent
 2. migrate_default_watchlist()        [OPS]   one-time helper, no-op afterwards
 3. batcher.start()                    [HOT]   equities OHLCV batch writer (500 rows / 5s)
 4. futures_batcher.start()            [HOT]   futures batch writer

LIVE TIER (soft-fail)
 5. stream_service.start()             [HOT]   opens the provider WebSocket; then starts the
                                                universe re-sync loop (STREAM_UNIVERSE_RESYNC_SECONDS,
                                                default 60, 0=off; only when a stream provider is set)
 6. _warm_names() + names refresh loop [OPS]   instrument_names warm; loop re-resolves blanks
 7. watchlist_service.start()          [HOT]   ⚠ inits the stream provider; soft-fails without creds
 8. live_lake_writer                   [COLD]  if LIVE_LAKE_WRITER_ENABLED → CH → lake every 5 min
 9. journal_sync_service.start()       [OPS]   if JOURNAL_ENABLED + Schwab creds → balances/trades every 5 min

SCHEDULED LOOPS (each sleeps to its hour; no external call at boot)
10. nightly_equities_polygon_refresh   [COLD]  POLYGON_NIGHTLY_ENABLED + STOCK_LAKE_BUCKET
11. nightly_schwab_refresh             [COLD]  SCHWAB_NIGHTLY_ENABLED + STOCK_LAKE_BUCKET
12. nightly_futures_refresh            [COLD]  FUTURES_NIGHTLY_ENABLED + STOCK_LAKE_BUCKET
13. options_snapshot_refresh           [COLD]  OPTIONS_SNAPSHOT_ENABLED — see the warning below
14. nightly_futures_polygon_refresh    [COLD]  FUTURES_POLYGON_NIGHTLY_ENABLED + STOCK_LAKE_BUCKET
15. news_ingest                        [OPS]   NEWS_INGEST_ENABLED → EDGAR/FOMC/BLS/BEA
16. nightly_elliott_recompute          [COLD]  ELLIOTT_RECOMPUTE_ENABLED + STOCK_LAKE_BUCKET
17. elliott_live_scanner               [HOT]   ELLIOTT_LIVE_SCANNER_ENABLED → wave alerts on each bar
18. ch_reconcile                       [COLD]  CH_RECONCILE_ENABLED + STOCK_LAKE_BUCKET → heals CH from the lake
19. broadcast_signal helper            [API]   WebSocket fan-out
20. yield → serve HTTP/WS
```

> **`OPTIONS_SNAPSHOT_ENABLED` must be true in exactly one process.** Two
> concurrent writers make every Iceberg append fail on optimistic concurrency —
> including the raw payload archive, which is the recovery source. Each
> worktree's `.claude/launch.json` ships it `false` for this reason. See
> [`runbook.md`](runbook.md).

Scheduled loops compute their own delay (`_seconds_until_next_run()`) and make
**no external call at boot**, so a restart during market hours costs nothing.
Each has in-process auto-catchup for runs missed while the process was down.

## Gates and what each one controls

| Gate | Where | What it enables |
|---|---|---|
| `DATA_PROVIDER` | `.env` | Default for live streaming + history REST. `STREAM_PROVIDER` / `HISTORY_PROVIDER` override per role. |
| Provider credentials (e.g. `ALPACA_API_KEY`, `SCHWAB_REFRESH_TOKEN`) | `.env` | Whether `watchlist_service` can actually subscribe to bars. Missing creds → soft-fail (logged ERROR + WARNING; app continues). |
| `JOURNAL_ENABLED` + Schwab creds | `.env` | Whether `journal_sync_service` polls Schwab account balances + trades into CH. |
| `STREAM_UNIVERSE_RESYNC_SECONDS` > 0 + a stream provider | `.env` | Whether `stream_service` re-reads `stream_universe` ∪ `futures_universe` on a timer and converges its live subscriptions on it. A process with no stream provider (the `api` container) never runs it. |
| `POLYGON_NIGHTLY_ENABLED` + `STOCK_LAKE_BUCKET` | `.env` | Whether the 07:00 UTC Polygon → lake loop runs. |
| `SCHWAB_NIGHTLY_ENABLED` + `STOCK_LAKE_BUCKET` + Schwab creds | `.env` | Whether the 22:00 UTC Schwab → lake loop runs. |

Note the symmetry: the two nightly jobs have identical gate shape and
behavior. Neither depends on `DATA_PROVIDER` — they have their own
provider-specific credentials and run independently.

## How to verify everything's running

After `Application startup complete`, scan the log for these lines.
Each one corresponds to one subsystem actually being live:

```
✅ ClickHouse schema ready                                # step 1
✅ OHLCV batch writer started                             # steps 3-4
✅ Stream service started                                 # step 5
Stream: universe re-sync loop started (every 60s)         # step 5 — STREAM_UNIVERSE_RESYNC_SECONDS>0 + a stream provider
✅ Watchlist service started (provider=X, symbols=N)      # step 7 — no ERROR above ⇒ streaming OK
✅ Live lake writer started                               # step 8  — LIVE_LAKE_WRITER_ENABLED
✅ Journal sync started (every 5min: balances + trades)   # step 9  — JOURNAL_ENABLED + Schwab creds
nightly_equities_polygon_refresh: background loop started # step 10 — POLYGON_NIGHTLY_ENABLED
nightly_schwab_refresh: background loop started           # step 11 — SCHWAB_NIGHTLY_ENABLED
nightly_futures_refresh: background loop started          # step 12 — FUTURES_NIGHTLY_ENABLED
options_snapshot_refresh: background loop started         # step 13 — OPTIONS_SNAPSHOT_ENABLED (one process only)
ch_reconcile: background loop started                     # step 18 — CH_RECONCILE_ENABLED
✅ Application startup complete
```

If a line is missing, the corresponding subsystem is OFF — usually
because of an env flag or missing credentials. The map above tells
you which gate to check.

## Cadence reference

| Job | Schedule | Direction |
|---|---|---|
| `streamer` (live WS) | continuous | provider WS → CH `ohlcv_1m` |
| `stream_universe_resync` | every 60 s (`STREAM_UNIVERSE_RESYNC_SECONDS`; 0 disables) | CH `stream_universe` ∪ `futures_universe` → live subscription set. Lets the `worker` pick up a row the `api` container wrote (deployment C2); heartbeat-audited, so no-op cycles write one `ingestion_runs` row per day |
| `batcher` | 500 rows or 5s, whichever first | in-memory queue → CH |
| `backfill_service` quick path | on watchlist change + every 15 min | provider REST → CH |
| `backfill_service` deep path | on demand via API | provider REST → CH |
| Backfill gap sweeper | daily 06:00 UTC | scans for CH holes, enqueues fixes |
| `journal_sync_service` | every 5 minutes | Schwab API → CH `account_snapshots`, `trades` |
| `lake_completeness_guard` | daily 23:00 UTC (`LAKE_GUARD_RUN_HOUR_UTC`) | CH Schwab-origin bars missing from the lake (last 14 days) → `schwab_universe` / `schwab_futures`; fails if a write does not verify |
| `ch_state_snapshot` | daily 01:00 UTC (`CH_STATE_SNAPSHOT_RUN_HOUR_UTC`) | every non-rebuildable CH table + `data/paper` → `s3://<lake>/ch_state/` (shrink-guarded, keeps 14) |
| `nightly_equities_polygon_refresh` | daily 07:00 UTC (midnight Arizona) | Polygon REST grouped-daily → `equities.polygon_daily_raw` |
| `nightly_schwab_refresh` | daily 22:00 UTC (3 PM Arizona) | Schwab pricehistory → `equities.schwab_universe` |

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
[main_api.py:177-220](../../app/main_api.py):

1. Cancel nightly_equities_polygon_refresh task
2. Cancel nightly_schwab_refresh task
3. `monitor_manager.stop_all()`
4. `watchlist_service.stop()`
5. `stream_service.stop()` (cancels the universe re-sync loop, unsubscribes everything)
6. `backfill_service.stop()`
7. `journal_sync_service.stop()` (if it was started)
8. `get_bar_batcher().stop()` (flushes pending writes)
9. `close_client()` (CH connection)

## Recommended improvements (not yet implemented)

Deferred items (track in commit messages as they ship):

1. **`/health/services` endpoint** — returns JSON status of every
   subsystem, grouped by tier. Removes the need to parse logs.
2. **Tier-grouped startup logs** — three section headers (`HOT TIER`,
   `COLD TIER`, `OPS`) so a single glance tells you what's healthy.
3. **Cleaner soft-fail for missing creds** — single WARNING line, not
   ERROR + WARNING + ✅.
4. **Compare delivered against subscribed.** `/stream/status` reports every
   subscribed symbol as subscribed, even the ones Schwab silently never
   delivers past its 300-symbol cap. Nothing currently compares the two
   numbers, which is why that cap hid for three months.
