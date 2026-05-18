# Streaming Universe Model — Concise

The operational model for what data sources cover which symbols
and how a new "streamed" symbol enters the system. Written as a
quick-reference; full architecture in
[silver_layer_plan.md](silver_layer_plan.md) and
[data_ingestion_paths.md](data_ingestion_paths.md).

---

## The model

```
                    HISTORICAL                          LIVE
                    ──────────                          ────
  POLYGON              Polygon flat-files               (not used)
                       Whole market × 5-20 years
                       (frozen when subscription pauses)
                              │
                              ▼
                       bronze.polygon_minute  (raw)
                              │
                              │
  SCHWAB               Schwab REST                      Schwab CHART_EQUITY stream
                       (nightly seed-universe refill)   (real-time, 1-min)
                       Seed universe only               Seed universe only
                              │                                  │
                              ▼                                  ▼
                       bronze.schwab_minute  (split-adjusted)
                              │
                              │
                       ┌──────┴──────────────────────────────────────┐
                       ▼                                              ▼
              silver_corp_actions_build              silver_ohlcv_build (TA-5.1, planned)
                      │                                              │
                      ▼                                              ▼
              silver.corp_actions ◄────── reads ──────  silver.ohlcv_1m + silver.bar_quality
                                                                     │
                                                                     │ silver_to_ch_backfill
                                                                     │ (TA-5.3, planned)
                                                                     ▼
                                                          ClickHouse (hot cache)
                                                          chart + screener reads
```

## The one streaming provider

**Schwab CHART_EQUITY WebSocket is the only live source.** No
Polygon stream, no Alpaca stream, no IEX. Symbols subscribed in any
active watchlist get streamed; everything else does not.

## The two-tier universe (what continues to grow)

| Tier | What's in it | Bronze coverage | Going forward |
|---|---|---|---|
| **Seed universe** | Symbols actively streamed by Schwab. Configured in `settings.seed_symbols`. ~100 today, grows. | Polygon archive (5-20y) + Schwab nightly fill (last 48d) + Schwab stream (forever) | New bars every minute via Schwab stream |
| **Archive only** | Everything else Polygon covered (rest of US market) | Polygon archive only | No new bars unless Polygon resumes |

## The "add a streamed symbol" flow

```
User: "Add NVDA to my live stream"
  │
  ▼
add_members("NVDA"):
  │
  ├── ALWAYS: subscribe NVDA on Schwab WebSocket
  │           → live ticks flow into CH (overlay)
  │           → live_lake_writer (TA-5.7 done) flushes to bronze every 5 min
  │
  └── HISTORY: branches by tier
      │
      ├── If NVDA ∈ seed_symbols:
      │     silver_to_ch_backfill(NVDA, days=730)
      │       → reads silver.ohlcv_1m (clean canonical history)
      │       → bulk-inserts into CH
      │       → ~10s wall-clock for 2y of 1-min data
      │     schwab_tip_backfill(NVDA)
      │       → bridges silver-watermark gap (≤48h Schwab REST)
      │       → writes bronze + CH
      │
      └── If NVDA ∉ seed_symbols (ad-hoc exploration):
            schwab_rest_one_shot(NVDA, days=48)
              → writes BRONZE only (not CH directly)
              → silver picks it up on next nightly silver_build
              → CH reads silver thereafter
            (chart shows live + Schwab REST 48d until silver builds)
```

## Promoting ad-hoc → seed

```
operator: $ scripts/promote_to_seed.py --symbol NVDA
            (or --universe sp500 to bulk-promote)
  │
  ▼
  1. Append to settings.seed_symbols
  2. Subscribe Schwab stream
  3. If Polygon active: ensure NVDA in next nightly flat-files import
     (Polygon flat-files contain every symbol anyway; importing one
      more is essentially free)
  4. Mark for inclusion in nightly Schwab refresh
```

**Strategic note:** before any planned Polygon-subscription pause,
bulk-promote (`--universe sp500` or `russell1000`) so every symbol
you might trade has Polygon-deep history locked in. After Polygon
pauses, newly-promoted symbols have a back-gap covering the pause
window.

## What changes when Polygon subscription pauses

Per [silver_layer_plan §2.4](silver_layer_plan.md) — three
operational states, all with zero code changes between them:

| State | Polygon bronze | Schwab bronze | Silver |
|---|---|---|---|
| **Active** (today) | growing nightly | seed × ongoing | whole market × 5-20y |
| **Paused** | frozen at pause date | seed × ongoing | whole market frozen + seed continues |
| **Resumed** | gap-fill backfill catches up | seed × ongoing | continuous |

The architecture is provider-pluggable. Pausing/resuming Polygon
is config + an ingest job stopping/starting, not code.

## What's built today vs what's needed

| Capability | Status | Code path |
|---|---|---|
| Schwab live stream → CH | ✅ live | bar_batcher → ohlcv_1m |
| Live → bronze (every 5min) | ✅ TA-5.7 | live_lake_writer |
| Polygon flat-files → bronze | ✅ live | nightly_polygon_refresh |
| Schwab REST nightly seed refresh → bronze | ✅ live | nightly_schwab_refresh |
| Polygon corp-actions → bronze → silver | ✅ TA-5.0 | corp_actions/{polygon_ingest, build}.py |
| **silver.ohlcv_1m** (canonical OHLCV) | ❌ **next: TA-5.1** | silver_ohlcv_build (to build) |
| **silver → CH backfill on add_members** | ❌ **next: TA-5.3** | silver_to_ch_backfill (to build) |
| `scripts/promote_to_seed.py` | ❌ planned | promote_to_seed.py (to build) |

**Path forward:** TA-5.1 → TA-5.3 → CH wipe-and-rebuild → universe-expansion CLI.

After all four land, the "add streamed symbol" flow becomes the
clean, fast, lake-canonical version above. The legacy
add_members-via-Schwab-REST-to-CH path (Path ② in
data_ingestion_paths) gets deleted.
