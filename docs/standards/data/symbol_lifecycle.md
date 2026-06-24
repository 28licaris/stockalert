# Symbol Lifecycle — Locked Architecture

**Status:** LOCKED. Read this before touching any ingest, universe, or
backfill code. Changes to the model require explicit signoff per
[engagement.md](../engagement.md). This doc supersedes prior
"add a streamed symbol" derivations in
[../../streaming_universe_model.md](../../streaming_universe_model.md)
and the retired v1 `data_platform_plan.md` (replaced by
[../../architecture_v2/](../../architecture_v2/README.md)).

## TL;DR

```
PROVIDERS                BRONZE (canonical archive)         SILVER (canonical OHLCV)        CH (derived hot cache)
─────────                ──────────────────────────         ────────────────────────        ──────────────────────
Polygon flat-files  ─>  bronze.polygon_minute               silver.ohlcv_1m  ─────────>  CH.ohlcv_1m
  (WHOLE MARKET,         (Iceberg on S3, immutable)         (corp-action adjusted,        (ReplacingMergeTree on
   nightly 07:00 UTC)                                        deduped, snapshot-pinned,     (symbol, timestamp))
                                                             1-minute resolution ONLY)
Schwab REST nightly ─>  bronze.schwab_minute  ┘             Chart resamples 1m at query
  (stream_universe,                                         time to 5m / daily / etc.
   nightly 22:00 UTC)
Schwab WS stream    ─>  CH.ohlcv_1m  ─[every 5min]─>  bronze.schwab_minute
  (stream_universe,                  live_lake_writer
   continuous)
Schwab REST tip-fill ─> bronze.schwab_minute + CH.ohlcv_1m
  (per-symbol on add,
   dynamic gap window)
```

Single canonical OHLCV resolution: `silver.ohlcv_1m`. CH stores 1m
only. The chart endpoint resamples to 5m/15m/30m/1h/4h/daily on the
fly via `toStartOf*()` aggregation. No `silver.ohlcv_5m` /
`silver.ohlcv_daily` schemas. No CH `ohlcv_5m` / `ohlcv_daily` writes
(those tables exist for back-compat but receive nothing new).

## The two paths

Every symbol's data flows through one of two pipelines.

### Quick path (T+0 to T+30s on add)

Triggered by `POST /api/v1/stream {"symbol": X}`. Goal: chart-ready
at every zoom level within ~30 seconds.

```
1. stream_universe row written (CH)
2. Schwab WS subscribe              → CH.ohlcv_1m forward (live ticks)
3. parallel:
   a. silver_ohlcv_build(X)         → silver.ohlcv_1m
      reads:  bronze.polygon_minute  (5y of 1-min, already there — Polygon nightly is whole-market)
              + bronze.schwab_minute (any prior Schwab data)
              + corp_actions
      cost:   ~5-15s for one symbol (single-symbol Iceberg scan + corp-action apply)
   b. schwab_rest_tip_fill(X)       → bronze.schwab_minute + CH.ohlcv_1m
      window: dynamic gap = max(silver_watermark, yesterday_polygon_close, now-48d) → now-1min
      cost:   ~3-8s
4. then silver_to_ch_backfill(X)    → CH.ohlcv_1m (bulk-insert from silver)
      cost:   ~2-5s
```

End state at T+30s: CH.ohlcv_1m has 5y of canonical 1-minute bars for
X. Chart renders at every zoom by resampling from CH.ohlcv_1m.

### Standard path (nightly batch, all of stream_universe)

Daily refresh of the whole hot universe. Keeps the canonical layers in
sync as new bars print and corp-actions land.

```
07:00 UTC   nightly_polygon_refresh
            spec: POLYGON_NIGHTLY_SYMBOLS = "all"  (WHOLE MARKET)
            yesterday's Polygon flat-file → bronze.polygon_minute
            auto-catchup: fills weekday gaps if uvicorn was down
            idempotent via lake_archive_watermarks

22:00 UTC   nightly_schwab_refresh
            spec: SCHWAB_NIGHTLY_SYMBOLS = "active"  (= stream_universe, see G1)
            Schwab REST /pricehistory → bronze.schwab_minute
            covers the 1-day gap between Polygon's T-1 and now

23:00 UTC   silver_ohlcv_build
            spec: SILVER_OHLCV_BUILD_SYMBOLS = "active"  (= stream_universe)
            bronze.polygon_minute + bronze.schwab_minute + corp_actions
            → silver.ohlcv_1m  (only for stream_universe symbols; bronze stays
               whole-market but silver is universe-bounded by design)
            idempotent via silver snapshot watermarks

23:30 UTC   silver_to_ch_refresh
            spec: stream_universe
            silver.ohlcv_1m (last 14 days) → CH.ohlcv_1m
            catches late-arriving data + corp-action backfills
            ReplacingMergeTree(version) handles overwrites cleanly
            full re-sync is operator-triggered (manual run via Job Registry)
```

## The medallion rules (immutable)

1. **Bronze is the canonical archive.** Per-provider, raw, immutable,
   append-only. Every provider write lands here. Read by silver only.

2. **Silver is canonical OHLCV.** Corp-action-adjusted, deduped across
   providers, snapshot-pinned. **One resolution: 1-minute.**
   Re-buildable from bronze any time. Read by silver_to_ch and any
   downstream consumer that needs canonical data.

3. **CH is derived hot cache.** Always re-buildable from silver.
   Allowed to be ahead of silver via live stream + tip-fill (the
   documented exceptions below). One canonical resolution: 1-minute.
   All other resolutions resampled at query time.

4. **Two bounded exceptions** to "no provider → CH direct":
   - **Live WebSocket**: Schwab CHART_EQUITY → CH.ohlcv_1m
     (live_lake_writer copies to bronze every 5 min).
   - **Schwab REST tip-fill**: ≤48d window on add, dual-writes to
     bronze.schwab_minute + CH.ohlcv_1m.

   Both fold back into the canonical pipeline within 24h (next
   silver_build picks up the bronze writes; silver_to_ch_refresh
   re-syncs CH).

## `stream_universe` is the single source of truth

The CH table `stream_universe` is **the** "what's our hot universe?"
input. Replaces the legacy `SEED_SYMBOLS ∪ active-watchlist-members`
derivation.

```
Read by:
  - nightly_schwab_refresh  (universe filter)
  - silver_ohlcv_build      (universe filter)
  - silver_to_ch_refresh    (universe filter)
  - Schwab WS subscription set (start-time)
  - get_active_universe()   (legacy callers)
  - cockpit Stream Service page

Written by:
  - stream_service.add()    (cockpit Add / `POST /api/v1/stream`)
  - stream_service.remove() (cockpit ✕ / `DELETE /api/v1/stream/{sym}`)
  - watchlist auto-extend   (adding a watchlist symbol not yet streaming)
```

Polygon nightly is **not** universe-bounded. It pulls the whole
market regardless of stream_universe — bronze.polygon_minute is the
historical reference for any symbol someone might add tomorrow.

## Failure modes + self-healing

| Failure | Recovery |
|---|---|
| uvicorn restart mid-stream | Live stream resumes from `start()`; backfill_gap_sweeper (06:00 UTC) fills the bar holes |
| Schwab WS dies (token / network) | Live ticks stop; nightly Schwab refresh (22:00 UTC) catches up within 24h; tip-fill on next operator action |
| Polygon nightly fails | Auto-catchup runs next night for missing weekdays; manual run via Job Registry |
| Silver build fails for one symbol | Other symbols still build; failed one retries next night; operator can manually re-trigger |
| CH wiped | Full re-sync from silver via `silver_to_ch_refresh` (manual full mode); only the last 24h of live ticks are unrecoverable |
| Corp-action adjustment changes historical bars | silver build re-snapshots; silver_to_ch_refresh's 14-day window catches recent changes; full re-sync for deeper history |
| Polygon subscription paused | bronze.polygon_minute freezes at pause date; Schwab nightly + WS keep stream_universe symbols current; on resume Polygon backfill catches up |

## The validation gate

A real integration test (`@pytest.mark.integration`) that fails CI if
the quick-path latency exceeds the target.

```
LOCKED TARGET: 5-year chart populated within 30 seconds of POST /api/v1/stream
EXPECTED:      ~15 seconds typical
```

Implementation: `tests/integration/test_add_new_symbol_latency.py`. Picks a fresh
symbol not in `stream_universe`, posts it, polls CH.ohlcv_1m until row
count ≥ thresholds at 30d / 270d / 5y windows. Records elapsed time
to `ingestion_runs` for the historical trend visible on the Status
page.

## What this replaces

| Legacy | Now |
|---|---|
| `SEED_SYMBOLS ∪ active-watchlist-members` (`get_active_universe`) | `stream_universe` table |
| `POLYGON_NIGHTLY_SYMBOLS = "seed"` (curated 100) | `POLYGON_NIGHTLY_SYMBOLS = "all"` (whole market) |
| `SCHWAB_NIGHTLY_SYMBOLS = "seed"` | `SCHWAB_NIGHTLY_SYMBOLS = "active"` (= stream_universe) |
| `SILVER_OHLCV_BUILD_SYMBOLS = "seed"` | `SILVER_OHLCV_BUILD_SYMBOLS = "active"` (= stream_universe) |
| Three Schwab REST warmups on add (1m / 5m / daily) | One: dynamic-gap tip-fill (1m only); silver covers the rest |
| `CH.ohlcv_5m` and `CH.ohlcv_daily` populated by warmup | Resample from `CH.ohlcv_1m` at query time |
| `silver_to_ch_backfill` only fires on `add` | Plus nightly `silver_to_ch_refresh` keeps CH fresh |

## When to update this doc

- Adding a new provider → update the bronze/silver mapping table.
- Changing the quick-path warmup → update the diagram + latency gate.
- Adding new resolutions → silver stays 1-min; CH resamples; no schema
  change unless a downstream system genuinely cannot handle 1m at
  query time (which would require its own justification).
- Anything that touches the medallion rules → explicit signoff required.
