# Spec — Lean Adjusted Storage (drop the materialized `polygon_adjusted` copy)

Status: **IMPLEMENTED** (2026-06-27) — decisions: pure compute-on-read (no
factor cache); coverage/read_arrow sourced from `polygon_raw`; materialized
`polygon_adjusted` table + weekly Spark job dropped. Equivalence gate passed
bit-for-bit before the drop.
Date: 2026-06-26

## 1. Problem

`equities.polygon_adjusted` stores a **full second copy of every price bar**
(~2.1B rows, whole-market × 5y): all OHLCV columns *plus* `adj_factor`
([schemas.py:114](app/services/equities/schemas.py)). But the adjusted
values are **fully derivable** from data we already store:

```
adj_factor(symbol, T) = ∏ split_factor_i   for splits with ex_date_i > date(T)
adjusted_price        = raw_price / adj_factor
adjusted_volume       = raw_volume × adj_factor      # mirror current job exactly
```

The split factors already live in `equities.market_corp_actions`
(`action_type='split'`). So `polygon_adjusted` is a derived copy of
`polygon_raw` — it violates the lean-storage principle (no stored column
recomputable from canonical inputs).

Costs of the copy:
- **~tens of GB of S3** duplicated.
- A **1–2h, ~$2–3 EMR Spark job** to (re)build it, which must re-run whenever
  splits change — and a split retroactively changes a symbol's *entire*
  history (back-adjustment), forcing whole-symbol rebuilds.
- The whole **run-tracking / incremental dirty-set** problem
  (docs: adjustment run-tracking) exists *only* to make rebuilding that
  copy cheap.

## 2. Goal

- **Stop storing adjusted prices.** Keep only canonical inputs: `polygon_raw`
  (prices) + `market_corp_actions` (splits).
- **Compute adjustment at read time** — cheap, because it's a per-symbol
  lookup over a handful of splits.
- Free the S3 space and **retire the weekly Spark adjustment job entirely**.
- **No change to consumer-visible output**: `AdjustedOhlcvReader.get_bars`
  / `get_bars_union`, `/api/v1/...adjusted`, and the MCP `adjusted_ohlcv`
  tool return byte-for-byte the same bars they do today.

## 3. Design

### 3.1 Read-time adjustment (the core)
Replace the `polygon_adjusted` scan in `AdjustedOhlcvReader` with:
1. Scan `polygon_raw` for `(symbol, window)` — same bucket+month pruning the
   adjusted table had, so single-symbol reads stay ~1/32 cost.
2. Load that symbol's splits from `market_corp_actions` (tiny — a handful).
3. Compute `adj_factor` per bar via `searchsorted` over the splits'
   cumulative product (the **exact algorithm the Spark job already uses** —
   `polygon_adjustment_job.py` cumulative-future-splits + broadcast lookup,
   lifted into a small pure function).
4. Apply `/factor` to prices, `×factor` to volume; emit the same `SilverBar`.

This shared transform lives in one pure function (e.g.
`app/services/equities/adjust.py::apply_adjustment(raw_arrow, splits)`),
reused by:
- `AdjustedOhlcvReader` (single-symbol reads)
- `read_arrow` (bulk / ML reads — vectorized in Polars)
- the Athena view (§3.3) for SQL consumers

### 3.2 Optional: tiny cumulative-factor cache (decide in review)
Computing the cumulative product per read is cheap, but if we want to skip
even that, materialize a **small** table:
`equities.adj_factors(symbol, ex_date, cum_factor)` — one row per split, not
per bar (thousands of rows, not billions). Refreshed instantly whenever
corp-actions change. **Recommend skipping** unless profiling shows the
per-read product matters — it almost certainly won't.

### 3.3 SQL / Athena consumers
Anything that queries adjusted prices via Athena (coverage, ad-hoc) needs a
replacement since there's no adjusted table:
- Provide an **Athena view** `polygon_adjusted_v` that joins `polygon_raw`
  to a splits-derived factor and computes adjusted columns, OR
- Point those consumers at `polygon_raw` + document the transform.
`athena_coverage` (used by `get_symbol_coverage`) should target
`polygon_raw` for the adjusted-history coverage numbers (row counts/min/max
are identical — adjustment doesn't add/remove rows).

### 3.4 Reproducibility
Today: pin one `polygon_adjusted` snapshot. After: pin **both**
`polygon_raw` and `market_corp_actions` snapshots. The reader's
`snapshot_id` becomes a composite (raw_snap, corp_actions_snap). Callers
that pin for reproducibility must record both.

## 4. Consumers to migrate (from grep)
`adjusted_ohlcv_reader.py` (get_bars, get_bars_union, get_symbol_coverage,
list_symbols, get_cross_provider_diff), `routes_adjusted.py`,
`mcp/tools/adjusted_ohlcv.py`, `readers/read_arrow.py`,
`readers/bars_gateway.py`, `readers/source_registry.py`,
`readers/lake_metadata_reader.py`, `equities/athena_coverage.py`,
`lake_to_ch_backfill.py`, plus docs in `docs/architecture_v2/`.

## 5. Migration / rollout
1. Land `apply_adjustment()` + unit tests.
2. **Equivalence gate (critical):** for a sample of symbols (incl. ones
   with splits — AAPL, NVDA, a recent reverse-split), assert read-time
   adjusted == current materialized `polygon_adjusted` bar-for-bar. This
   proves the on-read path reproduces the Spark job exactly before we delete
   anything.
3. Switch `AdjustedOhlcvReader` + read_arrow + Athena view to read-time.
4. Run all consumers' smoke tests.
5. **Only then** drop `equities.polygon_adjusted` (Glue table + S3 prefix) —
   frees the space. Keep a snapshot/backup reference until confident.
6. Retire `scripts/spark/polygon_adjustment_job.py` + its EMR wiring +
   `run_spark_job.sh` adjust path.

## 6. Consequences (mostly upside)
- **Frees ~tens of GB S3**; removes a 1–2h/$2–3 weekly EMR job.
- **Dissolves two other queued specs**: adjustment run-tracking and the
  incremental dirty-set detector become unnecessary — there's no copy to
  rebuild, so "when did it last run / what's dirty" no longer exists.
- Adjustment is always current the instant `polygon_raw` /
  `market_corp_actions` update — no lag, no nightly job.
- Slightly more compute per read (a tiny per-symbol factor calc); negligible
  for single-symbol, vectorized for bulk.

## 7. Open questions for sign-off
1. Pure compute-on-read (§3.1), or keep the tiny `adj_factors` cache (§3.2)?
2. Athena view vs point SQL consumers at raw + documented transform (§3.3)?
3. Confirm the volume transform (×factor) — mirror whatever the current job
   does, verified by the §5.2 equivalence gate.
4. Keep `polygon_adjusted` as a backup for one cycle, or drop immediately
   after the equivalence gate passes?
