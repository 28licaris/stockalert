# Streaming Universe Model — Concise

> **SUPERSEDED** by [`docs/standards/data/symbol_lifecycle.md`](standards/data/symbol_lifecycle.md)
> as of FE-CONTRACTS-4-final (2026-05-19). The locked architecture is
> documented there; this file is kept for historical context.
>
> Key differences from this doc:
>   - `stream_universe` (CH table) is canonical, not `SEED_SYMBOLS ∪ watchlists`.
>   - Polygon nightly = whole-market; Schwab nightly = stream_universe.
>   - Quick-path on add: on-demand silver build → silver→CH → Schwab tip-fill.
>   - Chart resamples 5m / daily from `ohlcv_1m` at query time (single
>     canonical resolution in silver).

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
              silver_corp_actions_build              silver_ohlcv_build (TA-5.1, LANDED)
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
| **Active universe** (G1, dynamic) | `SEED_SYMBOLS ∪ <every symbol in any active watchlist>`. Resolved at nightly-job time via `get_active_universe()`. ~100 today + growing as users + agents add watchlist members. | Polygon archive (5-20y) + Schwab nightly REST (last 48d) + Schwab stream (forever) when watchlisted | New bars every minute via Schwab stream + nightly bronze refresh for both providers |
| **Archive only** | Everything else Polygon covered (rest of US market, never watchlisted) | Polygon archive only | No new bars unless added to a watchlist or Polygon resumes whole-market |

**G1 LANDED 2026-05-17.** The `active` keyword is now a valid spec
for `POLYGON_NIGHTLY_SYMBOLS`, `SCHWAB_NIGHTLY_SYMBOLS`, and
`SILVER_OHLCV_BUILD_SYMBOLS`. Set those to `active` and adding any
symbol to any watchlist automatically grows nightly bronze + silver
coverage within 24h. **No separate "promote-to-seed" step needed.**

## The "add a streamed symbol" flow (unified — no two-tier branching)

**Single path for every symbol added for streaming.** No branching
on "is it in seed" — the same flow handles deep-history symbols
(seed-or-promoted) and brand-new ad-hoc symbols.

```
User: "Add NVDA to my live stream"
  │
  ▼
add_streamed_symbol("NVDA"):
  │
  │ 1. Subscribe Schwab CHART_EQUITY for NVDA.
  │    Live ticks now flow → CH live overlay AND (via
  │    live_lake_writer, every 5 min) → bronze.schwab_minute.
  │
  │ 2. silver_to_ch_backfill(NVDA, days=730)
  │    Reads silver.ohlcv_1m for NVDA, bulk-inserts into CH.
  │    - If silver HAS NVDA's history → fast (~10s for 2y), accurate.
  │    - If silver has NOTHING for NVDA (brand-new ad-hoc symbol) →
  │      no-op (writes 0 rows). Next step covers it.
  │
  │ 3. compute_gap_from_silver(NVDA):
  │      silver_watermark = max(ts) in silver.ohlcv_1m for NVDA
  │                       = None if symbol not in silver yet
  │      gap_start = max(silver_watermark, now - 48d)
  │                  (Schwab REST's 1-min reach is ~48 days)
  │      gap_end = now - 1 min  (avoid the in-flight minute)
  │
  │ 4. schwab_rest_tip_fill(NVDA, gap_start, gap_end):
  │    Pull Schwab REST /pricehistory for the gap window.
  │    Write to BOTH:
  │      - bronze.schwab_minute (idempotent upsert; immutable archive)
  │      - CH ohlcv_1m          (idempotent; chart available immediately)
  │
  │    This is the ONE bounded exception to "no historical → CH directly"
  │    — the window is ≤48 days, near-live, not bulk archive.
  │
  │ 5. Live stream takes over from gap_end onward.
  │    Chart now has: silver-derived history (if any) + Schwab REST
  │    tip-fill + live stream. Continuous from add-time → now.
  │
  │ 6. (automatic, no code needed)
  │    Next nightly silver_build sees the new bronze rows from step 4
  │    + live_lake_writer's contributions, merges them into silver.
  │    Subsequent silver_to_ch_backfill calls (e.g. when the user
  │    expands the chart window) re-sync CH from silver canonical.
```

**Why this is better than the two-tier design:**

1. **One code path.** No `if symbol in seed_symbols: ...` branching.
2. **Idempotent at every step.** Re-running the add flow is safe.
3. **Brand-new symbols get usable history immediately** (Schwab
   REST 48 days), not "wait 24h for nightly silver build".
4. **Self-healing.** Bronze gets the new rows on add; silver
   picks them up on next nightly; CH stays consistent.

**The ≤48 day Schwab REST limit is the one constraint.** A brand-new
symbol gets 48 days of 1-min history initially. To get deeper:
- Promote to the universe (so the next nightly Polygon refresh
  ingests its full history)
- Or run an operator-triggered one-shot Polygon pull for that symbol

After promotion + next nightly cycle, the symbol's silver history
extends back to Polygon's coverage (5-20 years).

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
| **canonical adjusted OHLCV** (was v1 `silver.ohlcv_1m`) | ✅ v2 — superseded the v1 silver build | `equities.polygon_adjusted` (Spark `polygon_adjustment_job.py`) + `/api/v1/adjusted/*` + MCP `get_adjusted_bars` |
| **lake → CH hydration** (was v1 silver→CH backfill) | ✅ v2 | `scripts/hotload_ch_from_lake.py` (bulk) + on-demand `app/services/equities/lake_to_ch_fill.py` + `schwab_tip_fill.py` |
| `scripts/promote_to_seed.py` | ❌ planned | promote_to_seed.py (to build) |

**Path forward:** TA-5.1.7 (operator validate + initial backfill) → TA-5.3 → CH wipe-and-rebuild → universe-expansion CLI.

After all four land, the "add streamed symbol" flow becomes the
clean, fast, lake-canonical version above. The legacy
add_members-via-Schwab-REST-to-CH path (Path ② in
data_ingestion_paths) gets deleted.
