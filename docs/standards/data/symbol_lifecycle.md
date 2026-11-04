# Symbol Lifecycle — Locked Architecture

**Status:** LOCKED. Read this before touching any ingest, universe, or
backfill code. Changes to the model require explicit signoff per
[engagement.md](../engagement.md).

Architecture context: [`../../architecture/README.md`](../../architecture/README.md).
Read-path detail: [`../../data/symbol_onboarding.md`](../../data/symbol_onboarding.md).

## TL;DR

```
PROVIDERS                      LAKE (Iceberg, ground truth)        CH (hot cache)
─────────                      ────────────────────────────        ──────────────
Polygon REST grouped-daily ─>  equities.polygon_daily_raw          CH.ohlcv_1m
  (WHOLE MARKET, nightly)      (raw, unadjusted)                   (ReplacingMergeTree
                                                                    on (symbol, timestamp))
Polygon flat-files (frozen) ─> equities.polygon_raw
  (minute history to 2026-06-12)                                   adjustment happens
                                                                   at READ time, not
Schwab REST nightly ────────>  equities.schwab_universe            on the way in
  (stream_universe scope)      (pre-adjusted, adj_factor=1.0)

Schwab WS stream ───────────>  CH.ohlcv_1m ─[every 5 min]─> equities.schwab_universe
  (stream_universe, live)                   live_lake_writer

Schwab REST tip-fill ───────>  equities.schwab_universe + CH.ohlcv_1m
  (per-symbol on add, ~48d window)
```

**One canonical resolution: 1-minute.** `CH.ohlcv_1m` is the only
equities serving table; the chart endpoint resamples to 5m/15m/30m/1h/daily at
query time (`queries.list_bars_resampled`, daily bucketed on the **ET** trading
day). There are no CH `ohlcv_5m` / `ohlcv_daily` *cache* tables — they were
retired because feeding them separately caused staleness and duplicate-candle
bugs. The `ohlcv_daily` / `ohlcv_30m` / `ohlcv_hourly` tables that exist are
**research** tables built by `scripts/build_ohlcv_*.py`, off the serving path.

## The three rules

1. **The lake is ground truth.** Per-provider, raw, append-only. Every provider
   write lands there. ClickHouse can be wiped and refilled from it.

2. **Derived data is computed on read, not stored.** Split adjustment is
   `adjusted = f(polygon_raw, market_splits)` via
   `app/services/equities/adjust.py`. The old materialized
   `equities.polygon_adjusted` table and its weekly job were retired. Do not
   reintroduce a materialized tier for anything recomputable.

3. **CH is a derived hot cache**, always rebuildable from the lake. It is
   *allowed* to be ahead of the lake via the two bounded exceptions below.

### Two bounded exceptions to "no provider → CH direct"

- **Live WebSocket** — Schwab `CHART_EQUITY` → `CH.ohlcv_1m`, with
  `live_lake_writer` copying to `equities.schwab_universe` every 5 minutes.
- **Schwab REST tip-fill** — a ~48-day window on add, dual-writing to
  `equities.schwab_universe` + `CH.ohlcv_1m`.

Both fold back into the lake within 24h, and `ch_reconcile` (23:00 UTC)
re-syncs CH from the lake to heal live-stream gaps.

## Adding a symbol

`POST /api/v1/stream {"symbol": X}`, or adding it to a watchlist (which
auto-extends the stream universe).

```
1. stream_universe row written (CH)
2. Schwab WS subscribe → CH.ohlcv_1m forward from now
3. One of two warmups, never both:
   • lake_warmup_enabled = false (DEFAULT)
       → hotload: last SYMBOL_HOTLOAD_DAYS (30) of 1m bars into CH via the
         provider-agnostic quick backfill. Non-blocking; <5s first paint.
         Disable with SYMBOL_HOTLOAD_ENABLED=false for pure stream-from-now.
   • lake_warmup_enabled = true
       → stream_service.add() runs the deep lake chain + tip-fill itself,
         and hotload is skipped to avoid a double fill.
4. Read-path gap-fill (SYMBOL_GAPFILL_ENABLED) covers a cold or brand-new
   symbol the lake cannot serve, via provider REST on first read.
```

Deeper history arrives lazily: `bars_gateway` detects short coverage and hands
the fill to `gap_fill_worker`, returning what CH has now. **The HTTP path never
blocks on a lake fill.**

Latency gate: `tests/integration/test_add_new_symbol_latency.py`
(`@pytest.mark.integration`) posts a fresh symbol and polls CH until row-count
thresholds are met, recording elapsed time to `ingestion_runs`.

## Nightly jobs

| UTC | Job | Scope | Target |
|---|---|---|---|
| 07:00 | `nightly_equities_polygon_refresh` | `POLYGON_NIGHTLY_SYMBOLS=all` — **whole market** | `equities.polygon_daily_raw` |
| 21:00 | `nightly_futures_polygon_refresh` | continuous roots | `futures.polygon_raw` → `polygon_continuous` |
| 22:00 | `nightly_schwab_refresh` | `SCHWAB_NIGHTLY_SYMBOLS=active` — `stream_universe` | `equities.schwab_universe` |
| 22:00 | `nightly_futures_refresh` | futures roots | `futures.schwab_futures` |
| 23:00 | `ch_reconcile` | `stream_universe` | CH (heals gaps from the lake) |
| 06:00 | `backfill_gap_sweeper` | streamed symbols | CH (intraday holes) |

Each has in-process auto-catchup for weekdays missed while the process was
down, is idempotent, and records to `ingestion_runs` through one shared
`audit_run` path — so a manual run and the scheduled loop log identically.

**Polygon nightly is not universe-bounded.** It pulls the whole market
regardless of `stream_universe`, because the lake is the historical reference
for any symbol someone might add tomorrow.

## `stream_universe` is the single source of truth

The CH table `stream_universe` is **the** answer to "what is our hot
universe?".

```
Read by:
  - nightly_schwab_refresh        (universe filter)
  - ch_reconcile                  (universe filter)
  - Schwab WS subscription set    (at start)
  - get_active_universe()         (app/services/universe/active_universe.py)
  - the Stream page

Written by:
  - stream_service.add()          (POST /api/v1/stream)
  - stream_service.remove()       (DELETE /api/v1/stream/{sym})
  - watchlist auto-extend         (adding a watchlist symbol not yet streaming)
  - theme/sector onboarding       (app/services/sectors/theme_store.py)
```

**No automatic bootstrap and no static fallback.** An empty table means an
intentionally empty universe. If ClickHouse cannot be read, universe resolution
**raises** and the dependent job or stream startup fails visibly. Operators
populate a new installation explicitly via `POST /api/v1/stream` or
`POST /api/v1/stream/import`.

**The universe is sticky.** Removing a symbol from a watchlist does *not*
unsubscribe it. Only `stream_service.remove()` does.

**Tracked instruments belong in the universe.** Adding a sector, theme or
symbol we track means adding its constituents to `stream_universe` — not just
backfilling CH. Otherwise `schwab_universe` never grows for them and gaps
accumulate.

## Coverage is not uniform

Schwab streams **300** equity symbols for this account — a hard account-level
ceiling on `CHART_EQUITY`, while `/stream/status` reports every subscribed
symbol as subscribed. Chunking does not raise it.

So `CH.ohlcv_1m` covers 300 symbols, not the whole universe, while the
research `ohlcv_daily` covers 415 because the daily path uses REST, which is
uncapped. **Never resample 1m into daily expecting full coverage.** Filed as
`schwab-300-symbol-account-cap` in [`../../ISSUES.md`](../../ISSUES.md).

## Failure modes + self-healing

| Failure | Recovery |
|---|---|
| uvicorn restart mid-stream | Live stream resumes at `start()`; `backfill_gap_sweeper` (06:00) fills bar holes; `ch_reconcile` (23:00) re-syncs from the lake |
| Schwab WS dies (token / network) | Live ticks stop; nightly Schwab refresh catches up within 24h |
| **Schwab refresh token expired (~7d life)** | Re-authorize, **then restart uvicorn** — the process caches the token at startup. A healthy stream does *not* mean healthy auth: the WS authenticates once at connect, so bars keep flowing while every REST call fails |
| Polygon nightly fails | Auto-catchup runs next night for missing weekdays; manual run via the Job Registry |
| CH market-data wiped | Rebuild from the lake with `scripts/rebuild_ch_from_lake.py`. `stream_universe` is CH-only — recreate it from `data/stream_universe_seed.csv` **first**, since the loader fills exactly that row set |
| `stream_universe` unavailable | Stream startup and universe-bounded jobs fail loudly. Restore ClickHouse access; do not substitute a static list |
| Corp-action changes historical bars | Nothing to rebuild — adjustment is computed on read from `market_splits` |
| One universe symbol pre-loaded before an auto-catchup | **Poisons gap detection**: `latest_loaded_date` sees it and skips that day for every *other* symbol. Verify credentials with a non-universe symbol |

## When to update this doc

- Adding a provider → update the TL;DR map and the nightly table.
- Changing the add-symbol warmup → update that section and the latency gate.
- Adding a resolution → CH stays 1-minute and resamples. A new stored
  resolution needs its own justification and signoff.
- Anything touching the three rules → explicit signoff required.
