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

## 2. Architecture (the canonical pipeline, no bugs)

### 2.1 The ground-truth rule

> **S3 silver is canonical ground truth. ClickHouse is a derived
> hot cache. Historical data NEVER enters CH directly from a
> provider — only via silver→CH backfill.**

Every architectural choice below derives from this rule. The rule
eliminates an entire class of consistency bugs (provider-fed CH
vs. provider-fed silver disagreeing) and makes CH **fully
rebuildable from silver byte-identically**.

| Source → destination | Bronze | Silver | CH |
|---|---|---|---|
| Polygon flatfiles bulk historical | **YES** | derived by silver_build (nightly) | **NO** |
| Schwab REST historical backfill | **YES** | derived by silver_build (nightly) | **NO** |
| Live stream (Schwab, 1-min) | **YES** (real-time append) | derived by silver_build (nightly, claims overnight) | **YES** (live overlay zone) |
| `silver_to_ch_backfill` (on user add_members) | — (read source) | — (read source) | **YES** (silver→CH path) |
| User-driven historical chart expansion | — | — (read source) | **YES** (silver→CH path) |

**Only two paths write to CH:**
1. **Live stream**: Schwab 1-min WebSocket → CH `ohlcv_1m` (real-time, marked `is_live=true`).
2. **silver_to_ch_backfill**: on `add_members` or chart-window expand.

Everything else goes through bronze + silver. **No historical
provider pull ever lands directly in CH.**

### 2.2 Provider strategy (live vs historical) — with volumetric scope

The provider topology is asymmetric on **two axes** at once:
- **Live vs. historical** (cost / freshness needs differ)
- **Whole-market vs. seed-only** (symbol coverage differs)

| Concern | Provider | Volumetric scope | Why |
|---|---|---|---|
| **Live 1-min bars** | **Schwab WebSocket** (`CHART_EQUITY`) | **Seed universe only** (~100 today, growing) | We pay for Schwab; stream included; no extra cost per seed symbol. |
| **Historical bulk archive** (one-shot) | **Polygon flat-files** | **Whole market** (~10K+ symbols × 5-20 years) | Polygon flat-files are per-day files containing every symbol; importing 1 symbol or 10,000 is the same scan cost. |
| **Historical tip-fill** | **Schwab REST `pricehistory`** | **Per-symbol on demand** (silver-watermark → live, ≤48h) | Schwab REST in the subscription; small windows; no rate-limit pressure. |
| **Schwab REST one-shot for ad-hoc** | **Schwab REST `pricehistory`** | **Per-symbol on demand** (≤48 days 1-min + multi-year daily) | When user adds a non-seed symbol, this gives Schwab's max-depth historical. |
| **Corp actions** | **Polygon REST** | **Whole market** | Polygon's corp-actions API is canonical. Snapshot once into `silver.corp_actions`. |
| **Polygon stream (live)** | **NOT USED** | n/a | Costs extra; live is solved by Schwab. |

### 2.3 Three temporal regimes — what the silver layer looks like over time

The architecture handles the Polygon-subscription lifecycle gracefully.
There are three regimes the system passes through:

**Regime 1 — Today** (Polygon active, 5y historical):

| Data | Source | Scope |
|---|---|---|
| `bronze.polygon_minute` | Polygon flat-files, **nightly** | Whole market × 5 years (growing) |
| `bronze.schwab_minute` | Schwab stream + REST | 100 seed × ongoing live + 48 days REST history |
| `silver.ohlcv_1m` | merged from both bronzes | Whole market × 5 years (Polygon-derived) + seed live overlay |

Adding any ticker to a watchlist: silver has it → `silver_to_ch_backfill`
populates the chart in ~10s.

**Regime 2 — During the 20-year upgrade** (one-shot bulk pull):

| Data | Source | Scope |
|---|---|---|
| `bronze.polygon_minute` | Polygon flat-files bulk backfill | Whole market × **20 years** (extends back to ~2003) |
| Other tables | unchanged | unchanged |

This is a one-shot operator action while Polygon subscription is
upgraded. Bronze grows to ~40-80 GB (still trivial). Silver build
processes the new partitions on subsequent nightly runs (or
operator-triggered catch-up).

**Regime 3 — After Polygon subscription drops** (steady state):

| Data | Source | Scope |
|---|---|---|
| `bronze.polygon_minute` | **FROZEN** — no new appends after Polygon-drop date | Whole market × 20y, static |
| `bronze.schwab_minute` | Schwab stream + REST | Seed universe (~100, growing) × ongoing |
| `silver.ohlcv_1m` | merged from both | Whole market × 20y (Polygon-derived) **plus** seed × ongoing (Schwab-derived) |

**No backend code changes** at the Polygon-drop moment. The nightly
Polygon job stops naturally (no new flat-files arriving / subscription
inactive). Silver build keeps running on whatever bronze provides.
Backtests over historical windows work unchanged (silver still has
the data). Only consequence: a non-seed ticker added AFTER the drop
sees its silver history frozen at Polygon-drop date for everything
prior, plus a `[Polygon-drop, now-48d]` gap for the period between
drop and Schwab REST's 48-day reach.

### 2.4 The two-tier universe (refined for the temporal regimes)

The two tiers are about **what continues to grow**, not historical depth:

| Tier | Membership | Historical depth | Going forward |
|---|---|---|---|
| **Seed universe** (~100, growing) | Symbols the operator chooses to stream live | Polygon archive (5→20y, frozen at drop) + Schwab REST tip + Schwab stream (forever) | New bars every minute via Schwab |
| **Ad-hoc / archive** (everything else Polygon ever covered) | Whatever Polygon flat-files contained | Polygon archive (5→20y, frozen at drop) | **No new bars** after Polygon drop. To get fresh data: promote to seed. |

The user can **grow the seed universe** by adding symbols to
`settings.seed_symbols`. Each addition consumes one Schwab stream
subscription slot (Schwab supports hundreds; functionally
unlimited at our scale). Schwab has no per-symbol incremental
cost.

**Why this matters for the cockpit:**

- A symbol's silver coverage is shown on the Status page as one
  of three states: 🟢 **seed (ongoing)**, 🟡 **archive (frozen at YYYY-MM-DD)**, ⚪ **never seen**.
- Adding a ticker shows its source: "Schwab stream + Polygon archive
  (last bar 2030-04-15)" vs "Schwab stream only (no Polygon history)".
- "Promote to seed" is a single-click operation in the cockpit.

### 2.5 The data flow (live + historical, unified)

```
                                  PROVIDERS
            ┌──────────────────────────────────────────────────────┐
            │ Schwab CHART_EQUITY stream    Schwab REST pricehistory│
            │ (live 1m, every ticker        (historical, ≤ 48d 1m, │
            │  in any watchlist)              multi-year daily)    │
            │                                                       │
            │ Polygon flat-files (nightly, seed universe ONLY,      │
            │ while Polygon subscription is active)                 │
            │                                                       │
            │ Polygon corp-actions REST (while subscribed)          │
            └──────────────────────┬───────────────────────────────┘
                                   │
                  ┌────────────────┼──────────────────┐
                  │                │                  │
                  ▼                ▼                  ▼
       ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐
       │ Live tick to CH  │  │  Append to   │  │ Append to        │
       │ (Schwab stream)  │  │  bronze.     │  │ silver.          │
       │                  │  │  *_minute    │  │ corp_actions     │
       │ Bar batcher      │  │  (Iceberg,   │  │ (Iceberg)        │
       │ → ohlcv_1m       │  │  append-only)│  │                  │
       │ (is_live=true,   │  │              │  │                  │
       │  live overlay    │  │              │  │                  │
       │  zone)           │  │              │  │                  │
       └────────┬─────────┘  └──────┬───────┘  └────────┬─────────┘
                │                   │                   │
                │                   └────────┬──────────┘
                │                            │
                │             ┌──────────────▼─────────────┐
                │             │ silver_build (nightly)     │
                │             │ - merge provider precedence │
                │             │ - apply corp-action adjust │
                │             │ - MERGE INTO silver.ohlcv_1m│
                │             │ - APPEND silver.bar_quality │
                │             │ - claims overnight any      │
                │             │   live-overlay rows from    │
                │             │   prior day                 │
                │             └──────────────┬─────────────┘
                │                            │
                │                            ▼
                │                ┌────────────────────────┐
                │                │  silver.ohlcv_1m       │
                │                │  (Iceberg, canonical,  │
                │                │   snapshot-pinnable)   │
                │                └───────────┬────────────┘
                │                            │
                │                            │ silver_to_ch_backfill
                │                            │ on add_members + on chart-window
                │                            │ expand. NEVER from a provider.
                │                            ▼
                └─────────►┌────────────────────────────────┐
                           │  ClickHouse ohlcv_1m            │
                           │  ─────────────────────         │
                           │  LIVE OVERLAY ZONE              │
                           │  (Schwab stream, is_live=true,  │
                           │   ~24h sliding window)          │
                           │                                 │
                           │  HISTORICAL ZONE                │
                           │  (silver-derived only)          │
                           │                                 │
                           │  Resampled: ohlcv_5m, ohlcv_15m │
                           │   … ohlcv_daily (MV-driven)     │
                           └───────────────┬─────────────────┘
                                           │
                                           ▼
                            ┌──────────────────────────────┐
                            │  IndicatorReader on demand   │
                            │  SMA, EMA, RSI, MACD, ATR,   │
                            │  Bollinger, …                │
                            └──────────────┬───────────────┘
                                           │
                                           ▼
                                Chart with overlays
```

### 2.6 The add_members flow, branched by tier

```python
def add_members(name: str, symbols: list[str]) -> dict:
    # 1. Standard watchlist DB row + stream subscribe.
    newly = watchlist_repo.add_members(name, symbols)
    self._reconcile()  # subscribes Schwab stream → CH live overlay

    # 2. For each newly added symbol, pick the right backfill path.
    for sym in newly:
        if sym in settings.seed_symbols:
            # SEED PATH — silver has full history.
            silver_to_ch_backfill.enqueue(sym, days=730)
            schwab_tip_backfill.enqueue(sym)  # silver-watermark gap
        else:
            # AD-HOC PATH — silver may have nothing for this symbol.
            schwab_rest_one_shot.enqueue(sym, days=48)  # → bronze
            # ↑ silver picks this up on next nightly build.
            # In the meantime, the chart shows live + Schwab REST
            # (read via SchwabReader, not CH) until silver populates.
            silver_to_ch_backfill_when_ready.enqueue(sym, days=48)
```

The ad-hoc path is graceful: the chart works immediately (live
stream + what Schwab REST gives us), and the next nightly silver
build promotes the data into the canonical store. If the user
adds the symbol to the seed universe later, a deeper backfill
runs at that point.

### 2.7 Race conditions and idempotency (the "no bugs" guarantees)

Six concrete races to defend against:

1. **Live tick arrives in CH before silver→CH backfill completes for the same minute.**
   - **Defense:** silver→CH backfill writes only rows where `ts < latest_live_tick_ts - 1m`. Live and historical zones don't overlap in time.

2. **silver→CH backfill writes a stale row over a fresher live row.**
   - **Defense:** Same as #1. silver→CH never touches the live overlay zone.

3. **Nightly silver_build claims a CH live-overlay row that's "wrong" (provider correction came in later).**
   - **Defense:** silver_build is idempotent via Iceberg `MERGE INTO`. Late corrections (Polygon pricehistory revisions for the previous day) trigger the affected silver slice to rebuild and the next silver→CH refresh to overwrite CH. CH stays eventually-consistent with silver within 24h.

4. **Operator adds the same symbol to two watchlists.**
   - **Defense:** `watchlist_service` is already refcount-based (existing TA-3.x work). One stream subscription per symbol regardless of watchlist count. silver→CH backfill is idempotent (CH `INSERT IGNORE` on `(symbol, ts)`).

5. **Two parallel silver_build runs.**
   - **Defense:** Watermark table uses a lease (per-symbol-per-day). Second run sees "already in flight" and skips. Pinned by `tests/test_silver_build_concurrency.py`.

6. **CH gets wiped/rebuilt.** The user-visible "no historical data" gap.
   - **Defense:** A `scripts/rebuild_ch_from_silver.py` operator script. Reads silver for all watchlist symbols, bulk-inserts into CH. ~hour wall-clock for a full S&P 500 rebuild. CH is **always** rebuildable from silver because of the ground-truth rule.

### 2.8 Adjusted vs. raw prices

Silver carries both `_raw` and `_adj` columns (split + dividend
adjusted). CH receives only one set — configured globally:

- **Chart, indicator overlays, screener, backtest training:** see `_adj`.
- **Backtest replay-accuracy mode:** opt-in via `BacktestConfig.adjusted=False` → reads `_raw` from silver.
- **Default everywhere:** `_adj`.

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

### 6.2 The "tip" provider backfill (Schwab REST)

The silver build runs nightly. There's always a gap between silver's
watermark and "now." When `add_members()` runs at 14:00 ET, silver's
freshest row is ~38 hours old (last night's build, 02:00 ET). That
gap has to be filled from the provider.

- After `silver_to_ch_backfill` completes, the SAME service triggers
  a small **Schwab REST `pricehistory`** backfill for the window
  `[silver_watermark, now)`. This is small (max ~1.5 days of bars
  ≈ 600 1-min bars). Done in seconds; no rate-limit pressure.
- The tip-backfill writes to **both** bronze (`bronze.schwab_minute`)
  and CH (`ohlcv_1m`). Bronze gets the canonical row for silver to
  consume on the next nightly build; CH gets the row immediately so
  the chart works now.
- Writes to bronze here is the **one exception** to the
  "no-historical-to-CH-from-provider" rule — but it's not really
  historical. It's the "near-live" tip that bridges silver's
  watermark to the live-stream's first bar. The window is bounded
  to ≤ ~48 hours.
- Once the live stream subscription is active, "now" is covered
  organically by the stream.

### 6.3 The two add_members paths (seed vs ad-hoc)

The change inside `watchlist_service.add_members()` (today calls
`_enqueue_backfill(symbols, kind="quick", days=30)` etc.):

```python
# BEFORE (today)
if newly:
    self._enqueue_backfill(newly, kind="quick", days=30)
    self._enqueue_backfill(newly, kind="intraday", days=270)
    self._enqueue_backfill(newly, kind="daily", days=365 * 2)

# AFTER (TA-5 lands) — branches by seed-universe membership
for sym in newly:
    if sym in settings.seed_symbols:
        # SEED PATH (deep history available in silver)
        self._enqueue_silver_to_ch_backfill(sym, days=365 * 2)
        self._enqueue_schwab_tip_backfill(sym)
    else:
        # AD-HOC PATH (no silver history; Schwab REST one-shot)
        self._enqueue_schwab_rest_one_shot(sym, days=48)
        # ↑ Writes to bronze. Silver picks it up on next nightly.
        # Until then, the chart reads live (CH) + Schwab REST
        # (via a SchwabRestReader fallback). After silver is built,
        # silver_to_ch_backfill catches up to silver-watermark
        # in the chart.
```

The seed path is what 90%+ of additions look like (you have a
curated watchlist of ~100-500 active trading candidates). The
ad-hoc path is for exploration: a friend mentions a ticker, you
add it, you see live data immediately + ~48d of Schwab REST 1-min
history within seconds. If it earns its keep, you promote it to
the seed universe (`scripts/promote_to_seed.py --symbol X`) and
the next nightly Polygon flat-file pull (while Polygon is active)
gives you the deeper history.

### 6.4 Why this never violates the ground-truth rule

A reader might worry: "ad-hoc path writes to CH from Schwab REST,
which sounds like a violation."

It isn't, by two arguments:

1. **The tip-backfill writes to BOTH bronze and CH.** Bronze is the
   canonical landing; the CH write is a parallel-path optimization.
   On the next nightly silver_build, silver materializes from
   bronze and the silver→CH refresh "blesses" those CH rows. The
   path from provider → CH always has a bronze co-write.

2. **The volume is bounded.** ≤ 48 hours of 1-min data. Compare to
   the historical bulk pull, which is 20 years. The rule's intent
   ("historical archive never goes direct to CH") is about the
   *bulk archive*, not the *near-live tip*. Make this explicit in
   the rule statement: **"Historical archive (>48h old) never goes
   direct to CH. Near-live tip may dual-write."**

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

**Case A — symbol is in seed universe** (deep silver history exists):

1. User clicks "Add MSFT" in the cockpit (or `POST /api/watchlists/...`).
2. `watchlist_service.add_members()` runs.
3. Schwab CHART_EQUITY stream subscribes to MSFT → CH live overlay
   begins.
4. **NEW:** `silver_to_ch_backfill.backfill("MSFT", days=730)`
   reads ~2 yrs of silver bars, bulk-inserts into CH `ohlcv_1m`
   (~10s).
5. **NEW:** `schwab_tip_backfill("MSFT")` covers the gap between
   silver's watermark (last night) and now (~600 bars, <5s). Writes
   to both bronze and CH; next nightly silver_build absorbs.
6. Chart on `/symbol/MSFT` renders within ~10s with full 2y history.

**Case B — symbol is NOT in seed universe** (ad-hoc exploration):

1-3. Same as Case A.
4. **DIFFERENT:** `schwab_rest_one_shot("XYZ", days=48)` fetches
   ~48 days of 1-min from Schwab REST → bronze. ~20-30s.
5. Symbol page chart populates progressively as bars land:
   - Live ticks visible immediately (overlay zone).
   - Schwab REST history fills in as backfill completes.
6. **Next nightly silver_build** (~hours from now) materializes
   silver from the new bronze rows.
7. **Following day's `silver_to_ch_refresh`** rewrites the CH rows
   from silver (with corp-action adjustment etc.).
8. The chart looks the same to the user from day 2 forward.

**Promote ad-hoc to seed** (for deeper history):

```bash
poetry run python scripts/promote_to_seed.py --symbol XYZ
```

This:
- Adds XYZ to `settings.seed_symbols`.
- Triggers a Polygon flat-files pull for XYZ over the full history
  window (if Polygon subscription is active).
- After Polygon drop: triggers a Schwab REST pull for whatever
  Schwab's deeper history window provides (multi-year daily,
  multi-month 1-min for some symbols).
- Marks XYZ for inclusion in all future nightly refresh jobs.

### 9.4 Rebuilding ClickHouse from scratch (the ground-truth recovery)

Because S3 silver is canonical, CH can be wiped and reconstructed
at any time. Operator runbook:

```bash
# 1. Stop the live stream (so we don't race during rebuild)
poetry run python scripts/stop_live_stream.py

# 2. Wipe CH ohlcv_1m (or the entire CH schema if needed)
poetry run python scripts/wipe_ch_ohlcv.py --confirm

# 3. Rebuild from silver for every symbol in any watchlist
poetry run python scripts/rebuild_ch_from_silver.py \
    --symbols watchlist \
    --days 730

# 4. Restart live stream
poetry run python scripts/start_live_stream.py
```

Wall-clock for a 100-symbol watchlist rebuild: ~30 min (CH ingest
bound). Wall-clock for an S&P 500 rebuild: ~2 hours.

This procedure exists because of the ground-truth rule. CH having
a problem (corruption, mistaken schema migration, bad rollout) is
recoverable. Silver having a problem is not — which is why silver
has Iceberg snapshot pinning, MERGE INTO discipline, and the
`silver.bar_quality` audit ledger.

### 9.5 Expanding the seed universe (growing the Schwab stream set)

Over time, the operator grows the seed universe from ~100 symbols
to whatever's tractable for them (Schwab supports hundreds of
concurrent CHART_EQUITY subscriptions). Each addition gets ongoing
live 1-min data via Schwab.

**Promote a single symbol:**

```bash
poetry run python scripts/promote_to_seed.py --symbol MSFT
```

This:
1. Appends MSFT to `settings.seed_symbols`.
2. Subscribes MSFT on the Schwab CHART_EQUITY stream (live overlay
   starts immediately).
3. If Polygon subscription is **still active**: ensures MSFT is in
   the next nightly Polygon flat-files import (already covered —
   the flat-files contain every symbol, importing one more is free).
4. If Polygon subscription has **already dropped**: triggers a
   Schwab REST one-shot for whatever Schwab's deeper history
   provides (multi-year daily, ~48 days 1-min). Bronze gets these.
5. Marks MSFT for inclusion in all future nightly Schwab refresh
   jobs (filling minute-bar gaps from the stream if any).

**Promote multiple symbols at once:**

```bash
poetry run python scripts/promote_to_seed.py \
    --symbols MSFT,GOOG,META,AMZN,NFLX
```

**Maximizing the seed universe before Polygon drops** (recommended
strategic action):

The marginal cost of adding a symbol to seed while Polygon is still
active is essentially zero — Polygon flat-files cover the whole
market regardless. So if there's any chance you'll want a symbol
tradeable in the future, add it to seed NOW so it gets the full
historical depth via Polygon AND ongoing coverage via Schwab.

```bash
# Pre-Polygon-drop maximalist expansion: add the S&P 500
poetry run python scripts/promote_to_seed.py --universe sp500

# Or the Russell 1000:
poetry run python scripts/promote_to_seed.py --universe russell1000
```

After Polygon drops, the seed universe can still grow, but newly
promoted symbols will have a **back-gap**: their bronze.polygon_minute
coverage ends at Polygon-drop date, then nothing until ~48 days ago
(Schwab REST limit), then ongoing. The cockpit shows this gap on
the Symbol page coverage strip as an explicit "no data — Polygon
subscription ended before this symbol was promoted" band.

### 9.6 Triggering a silver rebuild after corp-action correction

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

### 9.7 Monitoring

The cockpit Status page (FE-1) gets four new health pills powered by
`silver.bar_quality` and the build watermark:

- **Silver freshness (seed):** last successful silver build age for
  seed symbols (target <24h). Alert when >36h.
- **Silver coverage (seed):** % of seed symbols with silver data
  through yesterday (target 100%). Alert when <99%.
- **Polygon subscription status:** active / inactive / drop date.
  When inactive, displays "Bronze.polygon_minute frozen as of YYYY-MM-DD"
  so the operator knows non-seed symbols are static.
- **Seed universe size:** symbol count + recent growth. Quick view
  to see "how many symbols am I streaming right now" + a sparkline
  of seed-universe size over the last 30 days.

The Symbol page coverage strip also color-codes per-day cells by
data tier:
- 🟢 **Polygon (whole market era)** — historical coverage from
  flat-files.
- 🔵 **Schwab live + REST tip** — current bar(s) from the stream.
- 🟡 **Polygon archive (frozen)** — bars from before the Polygon
  drop, for a non-seed symbol.
- 🔴 **Gap** — no data. For non-seed post-Polygon-drop symbols,
  the `[Polygon-drop, now-48d]` gap is visible.

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
