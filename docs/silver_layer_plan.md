# Silver Layer — Implementation Plan

The implementation contract for `silver.*` — the canonical, deduped,
corp-action-adjusted OHLCV view of the lake. Schema design lives in
[data_platform_plan.md §6](data_platform_plan.md); this doc is the
**how**: build job, reader, backfill integration, user stories,
phasing, operator runbook.

**Status:** plan only. No code written yet.

**Why now:** three independent tracks converge on silver. Each
becomes ~10× better when it lands.

| Consumer | What silver unblocks |
|---|---|
| **Cockpit "add ticker" UX** ([frontend_plan §5.2](frontend_plan.md)) | Replace 90-180s provider-REST backfills with 5-15s silver→CH backfills. No provider rate limit; no provider quirks (Good Friday 400s, etc.); chart populates in seconds. |
| **Backtest + training** ([trading_subsystem_design.md](trading_subsystem_design.md)) | Backtests stop reading raw bronze (which has split discontinuities and provider disagreements). Adjusted columns mean training data doesn't think a 4-for-1 split is a -75% return. |
| **Gold features + EW labels** ([elliott_wave_plan.md §EW-6](elliott_wave_plan.md)) | Gold tier (pre-computed indicator features, EW wave labels) reads from silver. Without silver, gold either re-implements adjustment logic or trains on dirty data. |

**Companion docs:**
- [data_platform_plan.md](data_platform_plan.md) — bronze→silver→gold
  tiering, silver schema (§6), original phasing (§13.3).
- [frontend_plan.md](frontend_plan.md) §5.2 — the warming-up UX
  that silver→CH backfill makes feasible.
- [trading_subsystem_design.md](trading_subsystem_design.md) — the
  backtest harness whose `BronzeReader` usage flips to `SilverReader`.
- [elliott_wave_plan.md](elliott_wave_plan.md) — depends on silver
  for `gold.elliott_wave_labels`.

---

## 1. The user story that drives this work

The single concrete user-visible reason silver moves from "Phase 3
of the data platform plan" to "actively useful this quarter":

> "I add **NVDA** to a watchlist. I navigate to `/symbol/NVDA`. The
> 1-day chart with SMA(50) and RSI(14) overlays renders in under 10
> seconds. The 1-minute chart renders within 30 seconds. I never
> see a 'no data' state. I never wait for Schwab's API to rate-limit
> me. The bars don't lie about historical splits."

Today that story takes 90-180s, hits provider rate limits, and shows
unadjusted prices. Silver makes it 10× faster, cheaper, and accurate.

That's the bar. Every architectural choice below traces back to it.

---

## 2. Architecture (the silver→CH flow on add)

```
                       ┌─────────────────────────┐
USER  ──► add NVDA ──► │ watchlist_service       │
                       │ .add_members()          │
                       └────────────┬────────────┘
                                    │
                  ┌─────────────────┴──────────────────┐
                  │                                    │
                  ▼                                    ▼
        ┌─────────────────┐               ┌──────────────────────────┐
        │ provider stream │               │ silver_to_ch_backfill    │
        │ subscribe       │               │ (NEW — replaces today's  │
        │ (Schwab/        │               │  3-job provider REST     │
        │  Polygon ws)    │               │  backfill)               │
        └────────┬────────┘               └────────────┬─────────────┘
                 │                                     │
                 │                                     ▼
                 │                       ┌──────────────────────────┐
                 │                       │ SilverReader.get_bars(   │
                 │                       │   symbol, start, end,    │
                 │                       │   *, snapshot_id=None    │
                 │                       │ )                        │
                 │                       │                          │
                 │                       │ Reads Iceberg            │
                 │                       │ silver.ohlcv_1m via      │
                 │                       │ PyIceberg + DuckDB       │
                 │                       └────────────┬─────────────┘
                 │                                    │
                 ▼                                    ▼
        ┌────────────────────────────────────────────────────┐
        │  ClickHouse ohlcv_1m (the hot cache)               │
        │                                                    │
        │  - Live bars (next-minute onward) from stream      │
        │  - Historical bars (years) from silver→CH backfill │
        │  - Resampled views: ohlcv_5m, ohlcv_15m, ...,      │
        │    ohlcv_daily                                     │
        └────────────────────┬───────────────────────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │  IndicatorReader (Pattern A) │
              │  computes overlays on demand │
              └──────────────────────────────┘
                             │
                             ▼
                  Chart with SMA, RSI, …
```

Three points to call out:

1. **The stream subscription doesn't change.** Provider WebSocket
   still feeds live 1-min bars into `ohlcv_1m`. Silver fills history;
   stream fills now-and-after.
2. **There's a brief overlap zone** where the most-recent silver row
   is older than the first streamed bar. Two cases:
   - **First live bar arrives BEFORE silver→CH backfill writes the
     most recent silver row**: the stream-side writer is idempotent
     (REPLACE on `(symbol, ts)`); silver-side writer is INSERT IGNORE.
     Whichever lands first wins the cell.
   - **Silver's latest row is older than the cutoff** where live
     stream started (e.g. silver built yesterday at 22:00 UTC, you
     added NVDA today at 14:00 UTC): there's a 16-hour gap. The
     stream fills bars from now; silver covers everything up to its
     watermark. **The gap between silver's watermark and the live
     stream's first bar is filled by a small "tip" backfill from
     the provider REST** (the same code path as today, but only for
     the silver-stale tail, not the full history).
3. **Adjusted vs. raw prices.** Silver carries both. CH receives a
   choice: either ingest the `_adj` columns (what charts + ML want)
   or the `_raw` columns (what the trader saw live). The default for
   the chart layer is `_adj`. The default for the backtest harness
   is also `_adj` for ML; `_raw` available behind a flag for replay
   accuracy.

---

## 3. The build job (`silver_build.py`)

Daily batch. Reads bronze, writes silver. Idempotent + incremental
via watermarks.

### 3.1 Inputs

- `bronze.polygon_minute` — Polygon flat-files + REST + future Polygon
  stream.
- `bronze.schwab_minute` — Schwab stream + REST backfills.
- `silver.corp_actions` — Polygon corp-actions API ingest (built
  alongside; see §4).
- Watermark: last-built `(symbol, day_partition)` cursor in CH
  `ingestion_runs`.

### 3.2 Outputs

- `silver.ohlcv_1m` — one row per `(symbol, ts)`. Adjusted + raw
  columns; provider precedence applied.
- `silver.bar_quality` — one row per `(symbol, date)` documenting
  data-quality metrics.

### 3.3 Algorithm (per `(symbol, day_partition)` slice)

```python
# Pseudocode — see app/services/silver/silver_build.py when built
def build_silver_slice(symbol: str, day: date) -> None:
    # 1. Read all provider bronze rows for this day.
    polygon_bars = read_bronze("polygon_minute", symbol, day)
    schwab_bars  = read_bronze("schwab_minute",  symbol, day)

    # 2. Merge with provider precedence.
    # Default order: polygon > schwab. First-with-row wins per minute.
    merged = merge_with_precedence(
        sources=[("polygon", polygon_bars), ("schwab", schwab_bars)],
        precedence=settings.silver_provider_precedence,
    )

    # 3. Look up corp-action factors for THIS symbol on EARLIER dates.
    factors = compute_adjustment_factors(
        symbol,
        as_of=date.today(),   # "today's view" of historical adjustments
        corp_actions=read_corp_actions(symbol),
    )

    # 4. Compute adjusted columns. Raw columns pass through unchanged.
    adjusted = apply_adjustment_factors(merged, factors)

    # 5. Compute bar-quality metrics for this slice.
    quality = compute_bar_quality(symbol, day, merged, polygon_bars, schwab_bars)

    # 6. MERGE INTO silver.ohlcv_1m  (PyIceberg upsert by (symbol, ts))
    silver_table.merge_into(adjusted, key=("symbol", "ts"))

    # 7. Append to silver.bar_quality
    quality_table.append([quality])

    # 8. Update watermark
    mark_silver_built(symbol, day)
```

### 3.4 Adjustment recomputation

**Key invariant:** when a corp-action lands (e.g. NVDA announces a
4-for-1 split, ex-date in 30 days), ALL historical silver rows for
NVDA need their `_adj` columns recomputed. Implementation:

- On ex-date crossing, `silver_build.py` re-emits the affected
  `(symbol, ts)` slice with new factors.
- This is the reason adjustment lives in silver, not bronze. Bronze
  is append-only; silver is `MERGE INTO`.
- An alternative would be view-based adjustment (compute `_adj` on
  the fly from raw + corp-actions). We rejected this because:
  - PyIceberg's read path doesn't support computed columns
    efficiently;
  - Backtests need byte-identical reproducibility (a corp-action
    update mid-run shouldn't change historical bars within the run).
  Materializing `_adj` once is simpler and reproducibility-friendly.

### 3.5 Scheduling

- Runs nightly at **02:00 ET** (after market close + Polygon
  flat-files arrive at ~01:00 ET).
- Per-symbol slices run in parallel (semaphore-bounded to avoid
  saturating S3 or CH).
- On-demand trigger via `POST /api/silver/build` (operator-only;
  no rate limit; used after corp-action ingestion or backfill
  catch-up).

---

## 4. Corp-actions ingestion (`silver.corp_actions`)

Smaller cousin of the silver build. Polygon's
`/v3/reference/dividends` and `/v3/reference/splits` are the canonical
source for US equities; their `pay_date`, `ex_dividend_date`, and
`split_from`/`split_to` populate `silver.corp_actions`.

- **Initial backfill:** one-shot script reading Polygon's full history
  for every symbol in `bronze.polygon_minute`'s distinct-symbols
  list. ~50K splits + ~3M dividends since 2003. Iceberg `INSERT INTO`.
- **Ongoing:** nightly job at 01:30 ET pulls the previous day's
  announcements; appends to `silver.corp_actions`.
- **Trigger:** any new corp-action row for a symbol marks every
  silver slice for that symbol as "needs rebuild." `silver_build.py`
  picks them up on its next run.

---

## 5. `SilverReader`

The reader-layer class. Sibling to `BronzeReader` /
`BarReader` / `IndicatorReader`.

```python
# app/services/readers/silver_reader.py — NEW

class SilverReader:
    """Read silver.ohlcv_1m via PyIceberg. Snapshot-pinnable
    for reproducibility (every read can pin a specific Iceberg
    snapshot_id; a re-read against the same snapshot returns
    byte-identical bars)."""

    @classmethod
    def from_settings(cls) -> "SilverReader": ...

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        adjusted: bool = True,
        snapshot_id: Optional[str] = None,
        limit: int = 50_000,
    ) -> list[SilverBar]:
        """Half-open window [start, end). Returns SilverBar rows
        from the chosen snapshot (default: latest). `adjusted=True`
        returns the `_adj` columns; `adjusted=False` returns `_raw`."""

    def list_symbols(
        self, *, since: Optional[datetime] = None,
    ) -> list[str]:
        """Distinct symbols with silver coverage since `since`
        (default: full history)."""

    def get_latest_snapshot_id(self) -> str:
        """Iceberg snapshot ID at time of call. Pin this for any
        operation that needs reproducibility."""
```

`SilverBar` is the Pydantic mirror of the silver schema, parallel
to `BronzeBar` and `LiveBar`.

### Where SilverReader is used

| Consumer | Today | After silver |
|---|---|---|
| Backtest harness | `BronzeReader` | `SilverReader` (adjusted=True for ML; False for replay-accuracy) |
| `silver_to_ch_backfill` | N/A | `SilverReader.get_bars(...)` → CH `INSERT` |
| Live monitor | `BarReader` (CH) | unchanged — CH still serves live |
| Indicator computation | `BarReader` (CH) | unchanged — CH still serves the chart |
| Coverage / freshness | `BronzeReader` | `SilverReader` (silver IS the canonical coverage) |

---

## 6. The new backfill mode: `silver_to_ch`

This is the code-side replacement for today's
`quick`/`intraday`/`daily` provider-REST backfills.

### 6.1 New class in `app/services/ingest/`

```python
# app/services/ingest/silver_to_ch_backfill.py — NEW

class SilverToChBackfill:
    """Bulk-copies historical bars from silver Iceberg to CH for a
    symbol + window. Idempotent (CH side uses REPLACE on (symbol, ts))."""

    @classmethod
    def from_settings(cls) -> "SilverToChBackfill": ...

    async def backfill(
        self,
        symbol: str,
        *,
        days: int = 365 * 2,
        adjusted: bool = True,
    ) -> BackfillResult:
        """Read SilverReader for the window, batch-insert into CH
        ohlcv_1m. Returns row counts, snapshot_id used, duration."""
```

Per-symbol budget for one call: ~2 years × 390 bars/day × 252 days/yr
≈ 200k bars. Bulk insert into CH takes ~5-15s wall-clock.

### 6.2 The "tip" provider backfill

The silver build runs nightly. There's always a gap between silver's
watermark and "now." When `add_members()` runs at 14:00 ET, silver's
freshest row is ~38 hours old (last night's build, 02:00 ET). That
gap has to be filled from the provider.

- After `silver_to_ch_backfill` completes, the SAME service triggers
  a small provider-REST backfill for the window
  `[silver_watermark, now)`. This is small (max ~1.5 days of bars
  ≈ 600 1-min bars). Done in seconds; no rate-limit pressure.
- Once the live stream subscription is active, "now" is covered
  organically by the stream.

### 6.3 Replacing the watchlist-service call

The change inside `watchlist_service.add_members()` (today calls
`_enqueue_backfill(symbols, kind="quick", days=30)` etc.):

```python
# BEFORE (today)
if newly:
    self._enqueue_backfill(newly, kind="quick", days=30)
    self._enqueue_backfill(newly, kind="intraday", days=270)
    self._enqueue_backfill(newly, kind="daily", days=365 * 2)

# AFTER (TA-5 lands)
if newly:
    self._enqueue_silver_to_ch_backfill(newly, days=365 * 2)
    self._enqueue_silver_watermark_tip_backfill(newly)  # provider, small
```

Same fire-and-forget posture; same idempotency guarantees; ~10×
faster end-to-end.

### 6.4 Resampled tables

CH's `ohlcv_5m`, `ohlcv_15m`, etc. are populated by Materialized
Views (existing) that consume from `ohlcv_1m`. Once 1m rows land,
the resampled tables are populated within seconds (CH's MV
machinery is fast).

So the silver→CH→MV pipeline covers every interval the chart cares
about from the same backfill. No separate `intraday` / `daily`
jobs needed.

---

## 7. Migration: how we get from today to this

We don't rip out provider-REST backfill. We add silver as an
**alternative path** and gate the switch behind a setting.

### 7.1 Coexistence flag

```python
# app/config/settings.py
backfill_strategy: Literal["provider_rest", "silver_to_ch"] = "provider_rest"
```

Today's default = `provider_rest` (current behavior unchanged).
After silver is built and validated = flip to `silver_to_ch`.

### 7.2 Validation gate

Before flipping the default, we run a **shadow comparison**:

- For 10 days, when a ticker is added, run both backfill paths in
  parallel (to a shadow CH database for the silver path).
- A nightly diff job compares row-by-row between the canonical CH
  table (provider-fed) and the shadow CH table (silver-fed).
- Disagreement metric: per-symbol per-day count of bars where
  `abs(silver.close - provider.close) > 0.001`.
- **Pass threshold:** ≤0.1% per-bar disagreement, all corp-action
  events accounted for, every gap explainable (market holiday,
  symbol not yet listed, etc.).
- If shadow passes, flip the flag. Roll back path = flip flag back.

### 7.3 Backfill history

Once `silver.ohlcv_1m` covers full history (Phase TA-5.1 below),
we re-run silver→CH for every symbol currently in any watchlist.
This is a one-shot bulk operation; ~2K symbols × 200K bars =
400M rows; bounded by CH ingest speed (~hour). Schedule overnight.

---

## 8. Phasing

### Phase TA-5.0 — Corp-actions ingestion (3 days)

Foundation: silver needs corp-actions to be useful.

- `app/services/silver/corp_actions_ingest.py`.
- `silver.corp_actions` Iceberg table (schema in
  [data_platform_plan.md §6](data_platform_plan.md)).
- Initial backfill of Polygon's full corp-actions history.
- Nightly job at 01:30 ET.
- HTTP route + MCP tool: `get_corp_actions(symbol)`.
- Tests: synthetic split + dividend cases.

**Gate:** `silver.corp_actions` contains every known split + dividend
for any symbol in `bronze.polygon_minute`'s distinct-symbols list.
Manual spot-check against Polygon UI for 10 random symbols.

### Phase TA-5.1 — Silver build job (5–7 days)

- `app/services/silver/silver_build.py`.
- `silver.ohlcv_1m` Iceberg table.
- Per-symbol provider precedence merge.
- Adjustment factor computation + application.
- `silver.bar_quality` writer.
- Watermarked + idempotent.
- Initial backfill for full bronze history (S3-bound, ~6-12 hrs
  one-shot, scheduled overnight).
- Nightly job at 02:00 ET.
- Tests: synthetic 2-provider input → expected merged output;
  synthetic corp-action → expected adjustment.

**Gate:** for 50 hand-picked symbols across high-volume liquid +
illiquid + multi-split histories, silver row counts match
expected (= calendar 1m bars during market hours + extended where
applicable). Adjusted closes match Yahoo Finance's adjusted-close
on dividend / split dates (within $0.01).

### Phase TA-5.2 — SilverReader + reads-flip (3 days)

- `app/services/readers/silver_reader.py` per §5.
- HTTP route: `GET /api/silver/bars` (mirrors `/api/lake/bars`).
- MCP tool: `get_silver_bars`.
- Flip backtester `BronzeReader` → `SilverReader(adjusted=True)`
  default (configurable to `_raw` per-run).
- Add silver reads to indicator reader / dashboard symbol page
  for the "full history" pull (not the recent N days, which
  stays on CH).

**Gate:** existing backtest configs (canary SMA, EMA-crossover,
RSI, Bollinger, MTF-EMA) produce **near-identical** metrics on
silver vs. bronze for windows with no corp actions, and **provably
different (better — splits don't tank returns)** metrics on
windows with corp actions.

### Phase TA-5.3 — Silver→CH backfill mode (3 days)

- `app/services/ingest/silver_to_ch_backfill.py` per §6.
- Watermark-tip provider backfill.
- Coexistence flag (`backfill_strategy`).
- One-shot script to re-backfill every existing watchlist symbol
  from silver.

**Gate:** Add a new symbol; observe `silver_to_ch` mode kicks in;
1d chart populates in <10s; 1m chart populates in <30s; live
stream tick lands cleanly atop the silver-backfilled history.

### Phase TA-5.4 — Shadow validation + flag flip (2 days)

- Shadow CH database setup.
- Nightly diff job comparing provider-fed vs silver-fed paths.
- Diff dashboard + alerts.
- 10-day soak.
- Flip the default flag.

**Gate:** 10 consecutive days of <0.1% per-bar disagreement;
zero unexplained gaps; corp-action events flow through both paths
correctly.

### Phase TA-5.5 — Retire provider-REST backfill paths (1 day, contingent)

After 30 days on `silver_to_ch` default with no incidents:
- Delete `_enqueue_backfill` provider-REST paths from
  `watchlist_service.add_members()`.
- Keep the provider-REST capability available via the
  `/api/backfill/*` HTTP routes (operator escape-hatch).
- Update the cockpit Status page to remove the "backfill in
  progress (provider-REST)" indicators.

---

## 9. Operator runbook

### 9.1 First-time silver build (initial backfill)

Once Phase TA-5.1 lands:

```bash
# Generate corp-actions baseline (~15 min)
poetry run python scripts/run_corp_actions_backfill.py --since 2003-01-01

# Run silver build for all symbols, all history (~6-12 hr)
poetry run python scripts/run_silver_initial_backfill.py \
    --symbols all \
    --start 2021-01-01 \
    --end yesterday \
    --parallel 8

# Verify
poetry run python scripts/check_silver_coverage.py --report
```

Expected output: per-symbol `(silver_rows, bronze_rows,
disagreement_pct)` table. Anything with `disagreement_pct > 0.5%`
gets investigated before flipping the default.

### 9.2 Ongoing operations

Silver build runs nightly at 02:00 ET via the existing nightly-job
infra in [`app/services/ingest/nightly_polygon_refresh.py`](../app/services/ingest/nightly_polygon_refresh.py).
Per-night build covers the previous day's 1m bars from both bronze
sources. Wall-clock: ~20 minutes for the current 100-symbol seed
universe.

### 9.3 Adding the Nth symbol after silver lands

1. User clicks "Add MSFT" in the cockpit (or `POST /api/watchlists/...`).
2. `watchlist_service.add_members()` runs as today.
3. **NEW:** `silver_to_ch_backfill.backfill("MSFT", days=730)`
   reads ~2 yrs of silver bars, bulk-inserts into CH `ohlcv_1m`
   (~10s).
4. **NEW:** `silver_watermark_tip_backfill` covers the gap
   between silver's watermark and now (~600 bars, <2s).
5. Stream subscription is active; live ticks flow into CH from
   here on.
6. Chart on `/symbol/MSFT` renders within seconds.

### 9.4 Triggering a silver rebuild after corp-action correction

If Polygon corrects a stale dividend or we discover a wrong split
factor:

```bash
# Override or correct the corp-actions row in silver.corp_actions
poetry run python scripts/correct_corp_action.py \
    --symbol AAPL --ex-date 2014-06-09 --factor 7.0  # Apple 7-for-1

# Rebuild affected silver slices
poetry run python scripts/rebuild_silver.py --symbol AAPL

# Re-flush CH from silver for any watchlisted instance
poetry run python scripts/silver_to_ch.py --symbol AAPL --days all
```

### 9.5 Monitoring

The cockpit Status page (FE-1) gets two new health pills powered by
`silver.bar_quality` and the build watermark:

- **Silver freshness:** last successful silver build age (target: <24h).
- **Silver coverage:** % of watchlist symbols with silver data
  through yesterday (target: 100%).

Alerts fire when freshness >36h or coverage <99%.

---

## 10. Reproducibility & no-look-ahead

Silver inherits the same invariants as the rest of the platform:

1. **Snapshot pinning.** Every `SilverReader.get_bars(...)` accepts
   an optional `snapshot_id`. Backtest configs pin one. Re-running
   the same `(snapshot_id, symbol, window)` triple returns
   byte-identical bars.
2. **Append + MERGE only, never overwrite.** The silver build job
   uses Iceberg `MERGE INTO` (correctness layer) on top of
   `INSERT INTO` (operational layer). Past snapshots remain
   readable; reproducibility holds.
3. **Build provenance.** Every `silver.ohlcv_1m` row carries
   `ingestion_ts`, `source_provider`, and `sources_seen`. The
   build job version is recorded in CH `ingestion_runs`. Any silver
   row is traceable back to (a) which bronze sources contributed
   and (b) which build version produced it.
4. **Backtester contract.** Reproducibility test: re-running the
   canary SMA backtest against the same silver snapshot produces
   byte-identical metrics. Pinned by
   `tests/test_silver_reproducibility.py::test_backtest_replay_byte_identical`.

---

## 11. Risks & open questions

### Provider disagreement on the same minute

Polygon and Schwab will sometimes report different bars for the
same `(symbol, ts)` — different fill quality, different inclusion
of after-hours, different volume reconciliation. Precedence rule
(`polygon > schwab`) decides who wins; `sources_seen` records the
loss.

**Mitigation:**
- Per-symbol disagreement count in `silver.bar_quality` so we can
  spot the cases that matter.
- If disagreement persists >1% over any week-long window,
  investigate; usually one provider has a misconfigured timezone
  or reconciliation pipeline.

### Corp-action data quality

Polygon's corp-actions API has known holes (some pre-2010 dividends
missing factors; some ETF distributions miscategorized). Adjusted
prices will be slightly off for affected symbols.

**Mitigation:**
- `silver.bar_quality.disagreements` tracks Yahoo-vs-silver
  adjusted-close deltas for a spot-check set; alerts on drift.
- Operator escape-hatch script to manually correct
  `silver.corp_actions` rows (§9.4).

### Storage cost of dual columns

Silver carries 8 price columns (4 raw + 4 adjusted) per row vs.
4 in bronze. ~2× the storage. At ~30GB bronze today, silver
projects to ~60GB. Trivial cost (~$1.50/month S3 Standard); flagged
only because it doubles eventually.

### Coverage gap during initial backfill

The ~6-12 hour initial silver backfill blocks the flip-the-default
phase. During that window, the watchlist `add_members` path is
still provider-REST. Once silver is full, we re-run silver→CH for
all current watchlist symbols and flip.

**Mitigation:** schedule the initial backfill over a weekend.

### Latency on adjustment recompute after a corp action

When NVDA does a 4-for-1 split, every historical NVDA silver row
needs `_adj` recomputed. That's 2-3 years × 390 bars × NVDA history
≈ 300k rows. PyIceberg `MERGE INTO` on that is ~30 seconds.
Acceptable. Not "live" — but corp actions land overnight via
Polygon, so silver rebuild during the 02:00 ET window covers them
naturally.

---

## 12. Decisions deferred until we hit them

1. **Schwab corp-actions as secondary source.** Polygon is primary;
   Schwab's pricehistory implicitly carries adjusted prices. We
   could cross-check Schwab against Polygon. Decide if
   `silver.bar_quality.disagreements` shows persistent issues.
2. **Per-symbol provider precedence overrides.** Some symbols
   (penny stocks, obscure ETFs) might have better Schwab data than
   Polygon. Decide if `silver.bar_quality` shows specific symbols
   where Polygon's coverage is poor.
3. **Silver retention.** Today: full history. Future: do we retire
   bronze after a certain age (say, 5 yrs) and keep silver only?
   Decide once silver is operational for 6 months and we trust
   reconstruction.
4. **Silver → silver_daily aggregation.** A `silver.ohlcv_daily`
   pre-aggregated from `silver.ohlcv_1m` would be faster for daily
   backtests. Worth doing? Decide after TA-5.3 measures actual
   backtest read latency.
5. **Multi-asset extension.** Today silver covers US equities + ETFs.
   Futures (Schwab's CHART_FUTURES feed) follow the same model.
   Crypto / FX live elsewhere. Decide when a strategy demands them.

---

## 13. Where this fits in the overall roadmap

Insertion in [trading_subsystem_design.md §10](trading_subsystem_design.md):

```
TA-4.3 Screener (LANDED 2026-05-17)
TA-5.0 Corp-actions ingestion       ← this plan, start here
TA-5.1 Silver build job             ← the big chunk
TA-5.2 SilverReader + reads-flip
TA-5.3 silver_to_ch backfill mode   ← unlocks cockpit warming-up
TA-5.4 Shadow validation + flip
TA-5.5 Retire provider-REST paths
TA-6   TA indicator gap-fill
TA-7   Gold features                ← reads from silver
TA-8   Universe history
...
```

TA-5.3 is the unlock the user story in §1 actually needs — the
cockpit warming-up UX. Phases 5.4 and 5.5 are de-risking and
cleanup.

**The frontend track stays parallel.** FE-1 can ship in parallel
with TA-5.0/5.1; the Status page can render silver-build health
indicators as soon as silver exists.

---

## 14. Decisions needed before TA-5.0 starts

Two questions.

### 14.1 Provider precedence default

Default in [data_platform_plan.md §6](data_platform_plan.md):
`polygon > schwab`. Confirm or change.

- **Confirm** → silver build uses Polygon's bar when both providers
  have one for `(symbol, ts)`; Schwab fills gaps.
- **Reverse** → Schwab primary, Polygon fallback. Reasonable if you
  trust Schwab's stream timing more.
- **Per-symbol** → set up the config table now; decide later
  per-symbol. Easy to add; mild extra complexity.

My recommendation: **default to `polygon > schwab`**, with the
per-symbol override mechanism built into the config plumbing from
day one (no schema change later, just config).

### 14.2 Adjustment default for chart vs. backtest

When CH ingests bars from silver, does the chart see `_raw` or
`_adj`?

- **Adjusted everywhere** (recommended): chart, screener,
  indicator overlays, backtest training all see adjusted prices.
  Backtest replay-accuracy is opt-in via `BacktestConfig.adjusted=False`.
- **Raw on chart, adjusted on backtest:** "what the trader saw"
  on the chart; "what the model trains on" in the backtest. More
  faithful to lived experience; more confusing for ML reproducibility.
- **Choice per page:** chart has a toggle, backtest has a flag.
  Most flexible; most surface area to maintain.

My recommendation: **adjusted everywhere**, with `_raw` accessible
via an opt-in flag for the small set of users (us, today) who care
about replay-accuracy.
