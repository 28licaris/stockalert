# Data Ingestion Paths — Complete Architecture

The definitive map of **every path data takes into the system**.
Every chart, backtest, agent decision, and screener result traces
back through these paths. Bronze is the foundation; CH is the hot
cache; silver is the canonical view.

**Last empirically verified:** 2026-05-17 (TA-5.0 + TA-5.7 LANDED).
**Run `scripts/audit_bronze.py`** to verify these paths are healthy
against the live lake.

---

## 1. The mental model — three writers, three destinations

Three durable destinations and the writers that target each:

| Destination | What lives here | Writers (count) |
|---|---|---|
| **S3 / Iceberg bronze** | Raw per-provider archive, immutable | 4 writers |
| **S3 / Iceberg silver** | Canonical merged + adjusted, derived | 1 writer |
| **ClickHouse (hot cache)** | Real-time live overlay + derived hot reads | 3 writers |

Plus the planned tier:
| (planned) **S3 / Iceberg gold** | ML features, EW labels, universe history | future |

---

## 2. Master diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            EXTERNAL PROVIDERS                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐  ┌──────────────────┐  ┌─────────────────────────┐  │
│  │ Schwab CHART_EQUITY │  │ Schwab REST       │  │ Polygon                 │  │
│  │ WebSocket           │  │ /pricehistory      │  │ - flat-files (S3)       │  │
│  │ (live, 1-min)       │  │ /accounts (journal)│  │ - REST /v2/aggs         │  │
│  │ seed universe only  │  │                    │  │ - REST corp-actions     │  │
│  └──────────┬──────────┘  └─────────┬─────────┘  └──────────┬──────────────┘  │
│             │                       │                       │                 │
└─────────────┼───────────────────────┼───────────────────────┼─────────────────┘
              │                       │                       │
              │ ① live stream        │ ② REST→CH (LEGACY     │ ③ flat-file / REST
              │                       │    retiring TA-5.5)    │
              │                       │                       │
┌─────────────┼───────────────────────┼───────────────────────┼─────────────────┐
│             ▼                       ▼                       ▼                 │
│      ┌────────────┐       ┌──────────────────┐    ┌───────────────────────┐   │
│      │bar_batcher │       │historical_loader │    │ polygon_flatfiles     │   │
│      │            │       │.persist_bars     │    │ + nightly_polygon     │   │
│      │ async      │       │                  │    │ _refresh              │   │
│      │ buffered   │       │ batched REST pull│    │                       │   │
│      └──┬─────┬───┘       └────┬─────────────┘    └─────┬─────────────────┘   │
│         │     │                │                        │                     │
│         │     │                │                        │                     │
│         │     ▼                ▼                        ▼                     │
│         │  ┌────────────────────────────────────────────────────┐             │
│         │  │           CH ohlcv_1m (hot cache)                  │             │
│         │  │  source = 'schwab-stream' | 'schwab' |             │             │
│         │  │           'polygon-rest' | (none for flatfiles)    │             │
│         │  └─────┬──────────────────────────────────────────────┘             │
│         │       │                                                              │
│         │       │ ④ every 5 min                                                │
│         │       │   (live_lake_writer; NEW TA-5.7)                             │
│         │       │   filter: source LIKE '*-stream'                              │
│         │       ▼                                                              │
│         ▼  ┌──────────────────────────────────────────────────────┐            │
│  ┌────────┴───┐                                                   │            │
│  │ bronze.*_minute   ◄────────────────────────────────────────────┘            │
│  │  ┌──────────────────────┐  ┌──────────────────────┐                         │
│  │  │bronze.polygon_minute │  │bronze.schwab_minute  │                         │
│  │  │ 2.1B rows, 5yr       │  │ 1.77M rows, ~48d     │                         │
│  │  │ source = "polygon-   │  │ source = "schwab"    │                         │
│  │  │   flatfiles"         │  │   | "schwab-stream"  │                         │
│  │  └──────────────────────┘  └──────────────────────┘                         │
│  │                                                                             │
│  │  ┌──────────────────────────┐                                              │
│  │  │bronze.polygon_corp_actions│  ◄── ⑤ Polygon REST corp-actions             │
│  │  │ (TA-5.0; identifier=     │      (corp_actions/polygon_ingest.py)         │
│  │  │  symbol, ex_date,        │                                              │
│  │  │  action_type)            │                                              │
│  │  └──────────────────────────┘                                              │
│  │                                                                             │
│  │           │                                                                 │
│  │           ▼ ⑥ silver_corp_actions_build (TA-5.0)                           │
│  │           ▼   bronze → silver via provider precedence                       │
│  │                                                                             │
│  │  ┌──────────────────────────┐                                              │
│  │  │silver.corp_actions       │  ◄── canonical consumer surface              │
│  │  │ (5,108 rows verified for │                                              │
│  │  │  NVDA-split test week)   │                                              │
│  │  └──────────────────────────┘                                              │
│  │                                                                             │
│  │  ┌──────────────────────────┐  ◄── ⑦ silver_ohlcv_build (PLANNED TA-5.1)   │
│  │  │silver.ohlcv_1m  (PLANNED)│      bronze.*_minute + silver.corp_actions   │
│  │  │ + silver.bar_quality     │      → adjusted + raw columns per cell       │
│  │  └──────────────────────────┘      provider precedence merge               │
│  │                          │                                                  │
│  │                          │                                                  │
└──┼──────────────────────────┼──────────────────────────────────────────────────┘
   │                          │
   │       ┌──────────────────┘
   │       │ ⑧ silver_to_ch_backfill (PLANNED TA-5.3)
   │       │   on add_members + on chart-window expand
   │       ▼
   │   ┌──────────────────────────────────────┐
   │   │  CH ohlcv_1m (silver-derived zone)   │
   │   │  (historical, pre-overlay)           │
   │   └──────────────────────────────────────┘
   │
   └─► chart timeframes (5m/15m/30m/1h/1d) — resampled from ohlcv_1m on read
```

Numbered paths ①-⑧ are described in detail in §3.

---

## 3. Path-by-path walkthrough

### ① Schwab CHART_EQUITY live stream → CH `ohlcv_1m` (live overlay)

**Trigger:** Continuous WebSocket subscription, established by
`watchlist_service` when a symbol is in any active watchlist
(refcounted).

**Code path:**
```
SchwabProvider WebSocket (CHART_EQUITY frame)
  → watchlist_service._on_bar(bar)
  → bar_batcher.add(row)     # buffers up to 500 rows / 5s
  → bar_batcher.flush()
  → queries.insert_bars_batch_async(rows)
  → ClickHouse INSERT INTO ohlcv_1m
```

**Row identity:**
- `source = "{provider}-stream"` (e.g. `"schwab-stream"`) — set in
  `watchlist_service.__init__` based on `effective_stream_provider`
  + `-stream` suffix.
- `timestamp` = the bar's ET-aligned minute.

**Latency:** Schwab tick → CH visible in seconds (WebSocket frame
arrival + 5-second batch flush ceiling).

**Scope:** Only the 100-ish symbols subscribed via watchlists (the
"seed universe"). Not whole-market.

**Where the code lives:**
- [app/services/live/watchlist_service.py](../app/services/live/watchlist_service.py) `_on_bar`
- [app/db/batcher.py](../app/db/batcher.py) `AsyncBarBatcher`
- [app/db/queries.py](../app/db/queries.py) `insert_bars_batch`

---

### ② Schwab REST `/pricehistory` → CH `ohlcv_1m`

> ⚠️ **LEGACY PATH — VIOLATES THE GROUND-TRUTH RULE.**
> Scheduled for retirement at **TA-5.5** once TA-5.3 (silver_to_ch_backfill)
> ships. This path exists today only because the bronze→silver→CH-derived
> architecture is not yet fully wired; the legacy `add_members` flow still
> uses provider REST → CH directly. See silver_layer_plan §6.3 + §8.

**Why this is a violation:** historical data (>48h) should land in
bronze first, get derived into silver, and reach CH only via
silver→CH backfill. Path ② pulls historical data from Schwab REST
and writes it **directly to CH**, bypassing bronze entirely. For
ad-hoc symbols (not in the seed universe), this means **the lake
never gets a record of them** — they live only in CH.

**Trigger today (pending TA-5.5 retirement):**
- `backfill_service` on demand (gap-fill, quick, deep)
- `add_members` path for ad-hoc symbols (~48d 1-min + multi-year daily)
- Cockpit "manual backfill" buttons

(Note: `nightly_schwab_refresh` is NOT a trigger for this path — it
writes to `bronze.schwab_minute` per Path ③'s family.)

**Code path:**
```
backfill_service._enqueue_quick(symbol, days)
  → backfill_service._run_quick / _run_deep
  → historical_loader.fetch_and_save(symbol, start, end, timeframe)
    → provider.historical_df(symbol, start, end, "1Min")   # Schwab REST
    → queries.insert_bars_batch_async(rows)                # CH writer (the violation)
```

**Row identity:**
- `source = "schwab"` (REST tag).
- `timestamp` = bar's UTC minute.

**Schwab REST window limits:**
- 1-min bars: **~48 days** lookback max (Schwab's hard limit).
- 5-min bars: ~270 days.
- Daily: multi-year.

**Future state (after TA-5.3):** `add_members(symbol)` will instead:

| Symbol | New path |
|---|---|
| In seed universe | `silver_to_ch_backfill` (silver → CH; fast, snapshot-pinned) + tip-fill (Schwab REST → bronze + CH for ≤48h window) |
| Ad-hoc (non-seed) | `schwab_rest_one_shot` writes to **bronze** (not CH); silver picks it up on next nightly build; CH reads silver thereafter |

The bounded `tip-fill` (≤48h, near-live) is the ONE legitimate
exception to the ground-truth rule — see silver_layer_plan §6.4.

**Where the code lives:**
- [app/services/ingest/backfill_service.py](../app/services/ingest/backfill_service.py)
- [app/services/ingest/historical_loader.py](../app/services/ingest/historical_loader.py)
- [app/providers/schwab_provider.py](../app/providers/schwab_provider.py) `historical_df`

---

### ③ Polygon flat-files + REST → `bronze.polygon_minute` (S3) + optionally CH

**Trigger:**
- Nightly: `nightly_polygon_refresh` at `POLYGON_NIGHTLY_RUN_HOUR_UTC`
  (default 7am UTC = 03:00 ET).
- Operator bulk: `scripts/polygon_flatfiles_bulk_backfill.py` (one-shot
  historical backfill).

**Code path (nightly):**
```
nightly_polygon_refresh.run_lake_refresh_loop
  → BronzeIcebergSink.for_polygon_minute()
  → flatfiles_backfill.backfill_window(date_range, sinks=[bronze_sink])
    → PolygonFlatFilesClient downloads daily Parquet
    → BronzeIcebergSink.write(df)
      → table.append(arrow)   # PyIceberg append to bronze.polygon_minute
```

**Row identity:**
- `source = "polygon-flatfiles"` (per the bronze audit empirically
  confirmed 2026-05-17; this is the only source value present in
  bronze.polygon_minute today).
- `timestamp` = bar's UTC minute. Polygon flat-files are
  **raw / unadjusted** (verified by the probe + audit 2026-05-17).

**Whole-market coverage:** Polygon flat-files contain EVERY symbol
that traded each day; the importer can ingest a subset or all of
them. Today's bronze.polygon_minute has 2.1B rows from 2021-01-04
→ 2026-05-15.

**Note: NEVER writes to CH directly** (per the ground-truth rule;
silver_layer_plan §2.1). The `--write-clickhouse-too` flag exists
on the bulk backfill script for operator escape-hatch use, but the
default is bronze-only.

**Where the code lives:**
- [app/services/ingest/nightly_polygon_refresh.py](../app/services/ingest/nightly_polygon_refresh.py)
- [app/services/ingest/flatfiles_backfill.py](../app/services/ingest/flatfiles_backfill.py)
- [app/services/bronze/sink.py](../app/services/bronze/sink.py) `BronzeIcebergSink`
- [scripts/polygon_flatfiles_bulk_backfill.py](../scripts/polygon_flatfiles_bulk_backfill.py)

---

### ④ CH `ohlcv_1m` → `bronze.schwab_minute` (NEW, TA-5.7)

**Trigger:** `live_lake_writer` background loop, every 5 minutes
(`LIVE_LAKE_WRITER_CYCLE_MINUTES`). Started by FastAPI lifespan if
`LIVE_LAKE_WRITER_ENABLED=true`.

**Why this exists:** Closes the 8-24h gap between Schwab live ticks
landing in CH (seconds) and silver_build seeing them. Pre-TA-5.7,
live ticks went only to CH; bronze got them via the next-day Schwab
REST backfill — 8-24h stale. Post-TA-5.7, bronze is ≤30 min stale
during market hours.

**Code path:**
```
live_lake_writer.run_forever (asyncio task)
  ↓ every cycle_minutes
  live_lake_writer.run_cycle(as_of=now)
    ↓ for each provider in _PROVIDER_CONFIG:
    LiveLakeWriter._read_ch(source_tag, window_start, window_end)
      → CH SELECT FROM ohlcv_1m WHERE source = '{tag}'
        AND timestamp > start AND timestamp <= end
        # window_end = as_of - 1 min (skip in-flight minute)
    LiveLakeWriter._upsert_bronze(table_short, arrow_table)
      → bronze.{provider}_minute.upsert(arrow)   # PyIceberg upsert by (symbol, timestamp)
    LiveLakeWriter._record_run(result)
      → CH INSERT INTO ingestion_runs
```

**Row identity:** Preserves CH's `source = "schwab-stream"` tag.
Adds `ingestion_ts` + `ingestion_run_id` for the audit trail.

**Idempotency:** Iceberg upsert by `(symbol, timestamp)`. Re-running
the cycle on the same window is a no-op; cycles overlap by
`(lookback_minutes - cycle_minutes)` so cycle-boundary bars are
caught by the next cycle.

**Provider-pluggable:** Adding a new live provider = one entry in
`_PROVIDER_CONFIG`. The class doesn't know which providers exist.

**Where the code lives:**
- [app/services/ingest/live_lake_writer.py](../app/services/ingest/live_lake_writer.py)
- Lifespan wiring: [app/main_api.py](../app/main_api.py) startup/shutdown

---

### ⑤ Polygon REST corp-actions → `bronze.polygon_corp_actions` (NEW, TA-5.0)

**Trigger:**
- One-shot: `poetry run python scripts/run_corp_actions_backfill.py --full`
- Nightly (planned cron): same script with `--nightly` at 01:30 ET.

**Code path:**
```
run_corp_actions_backfill.py (--full or --nightly)
  → PolygonCorpActionsBronzeIngest.backfill_full_history(since, until)
    → PolygonCorpActionsClient.collect_splits(since, until)
      → GET /v3/reference/splits (paginated; ~50K splits since 2003)
    → PolygonCorpActionsClient.collect_dividends(since, until)
      → GET /v3/reference/dividends (paginated; ~3M dividends since 2003)
    → PolygonCorpActionsBronzeIngest._dedupe_actions(actions)
      # collapse same-(symbol, ex_date, action_type) by summing cash_amount
      # NEW (caught by live test): some symbols pay regular+special on
      # same ex_date; both labeled CD by Polygon
    → bronze.polygon_corp_actions.upsert(arrow)
      # identifier = (symbol, ex_date, action_type)
```

**Row identity:**
- `source_provider = "polygon"`
- Identifier = `(symbol, ex_date, action_type)`. `action_type` is
  one of: `split`, `cash_dividend`, `lt_capital_gain`,
  `st_capital_gain`, `stock_dividend`, `spinoff`.

**Idempotency:** PyIceberg upsert + per-batch dedup. Re-running is
safe; revisions overwrite.

**Where the code lives:**
- [app/services/silver/corp_actions/polygon_ingest.py](../app/services/silver/corp_actions/polygon_ingest.py)
- [app/providers/polygon_corp_actions.py](../app/providers/polygon_corp_actions.py)
- [scripts/run_corp_actions_backfill.py](../scripts/run_corp_actions_backfill.py)

---

### ⑥ `bronze.*_corp_actions` → `silver.corp_actions` (NEW, TA-5.0)

**Trigger:** `silver_corp_actions_build`, invoked by the same
`run_corp_actions_backfill.py` script (after the bronze stage).

**Code path:**
```
SilverCorpActionsBuild.run_full() / run_since(date) / run_nightly()
  → For each provider in settings.silver_provider_precedence:
    SilverCorpActionsBuild._read_bronze("{provider}_corp_actions", since)
      # Skips silently if bronze table missing (provider not onboarded)
  → SilverCorpActionsBuild._merge_with_precedence([(provider, arrow), ...])
    # First-with-row wins per (symbol, ex_date, action_type)
  → SilverCorpActionsBuild._restamp_ingestion(merged, run_id)
    # Replace bronze's run_id/ingestion_ts with this build's
  → silver.corp_actions.upsert(merged)
    # identifier = (symbol, ex_date, action_type)
```

**Row identity:** Same shape as bronze. `source_provider` field
shows which provider won the merge (today always "polygon"; future
will include other providers).

**Provider-pluggable:** Adding a new corp-actions provider = new
`bronze.{provider}_corp_actions` table + new entry in
`SILVER_PROVIDER_PRECEDENCE` env var. **Zero changes to silver_build code.**

**Where the code lives:**
- [app/services/silver/corp_actions/build.py](../app/services/silver/corp_actions/build.py)

---

### ⑦ `bronze.*_minute` + `silver.corp_actions` → `silver.ohlcv_1m` (SUPERSEDED by v2)

**Status: SUPERSEDED.** The v1 "silver build" described below was never
shipped; v2 replaced it with the whole-market Spark adjustment job
(`scripts/spark/polygon_adjustment_job.py` → `equities.polygon_adjusted`)
plus on-demand `equities.polygon_adjusted` → ClickHouse hydration
(`scripts/hotload_ch_from_lake.py`, `app/services/equities/lake_to_ch_fill.py`).
See [`docs/architecture_v2/`](architecture_v2/README.md). The planning
notes below are retained for historical context only.

**Code path (planned):**
```
SilverOhlcvBuild.build_slice(symbol, day)
  → Read bronze.polygon_minute + bronze.schwab_minute for symbol+day
  → Per-provider normalize each row to BOTH _raw + _adj using
    silver.corp_actions cumulative factors:
      • polygon (raw): _raw = passthrough; _adj = raw × factor_to_today
      • schwab (split_adjusted): _adj = passthrough;
        _raw = adj × cumulative_split_factor(day → today)
  → Merge with provider precedence (polygon > schwab default)
  → Compute silver.bar_quality (expected vs actual bars, disagreements)
  → silver.ohlcv_1m.upsert(merged)   # identifier (symbol, ts)
  → silver.bar_quality.append(quality)
```

**Critical**: The per-provider normalization step is what makes
silver correct given mixed adjustment status (Polygon raw, Schwab
split-adjusted — verified by the probe 2026-05-17). The original
design assumed all bronze was raw; the probe + the schema change
to per-provider `ADJUSTMENT_STATUS` constants enable this.

---

### ⑧ `silver.ohlcv_1m` → CH `ohlcv_1m` (silver-derived) (PLANNED, TA-5.3)

**Status: NOT YET BUILT.** Per silver_layer_plan §6.

**Trigger (planned):** `watchlist_service.add_members()` calls
`silver_to_ch_backfill.backfill(symbol, days=730)` after subscribing
to the live stream.

**Code path (planned):**
```
silver_to_ch_backfill.backfill(symbol, days)
  → SilverReader.get_bars(symbol, start, end, adjusted=True)
  → arrow → CH bulk-insert (INSERT IGNORE on (symbol, ts) collisions)
  → Watermark-tip backfill: schwab REST for (silver_watermark, now-1min)
    if needed
```

**Why this exists:** Cockpit "add ticker" UX. Today symbols added
to a watchlist trigger a Schwab REST backfill (slow, rate-limited).
With silver→CH backfill, the chart populates from S3 in seconds
instead of minutes.

---

## 4. Read-side surfaces (where ingested data goes)

Reads ALWAYS go through one of these layers; **no consumer reads
bronze directly** except silver_build (per the consumer contract).

| Read source | Used by | Latency | Freshness |
|---|---|---|---|
| `CH ohlcv_1m` (live overlay) | Chart live ticker, watchlist banner | ms | seconds |
| `CH ohlcv_1m` (historical, silver-derived) | Chart history, screener, indicator overlays | ms | daily (silver_to_ch nightly) |
| `silver.ohlcv_1m` direct (Iceberg) | Backtest harness, ML pipelines, MCP tools | ~100ms | daily (silver_build nightly) |
| `silver.corp_actions` | Charts (adjustment markers), backtests, screener rules | ~100ms | nightly |
| `CH agent_runs` | Run registry / replay | ms | live |
| `CH ingestion_runs` (NEW TA-5.7) | Audit dashboards | ms | live |

---

## 5. Audit + monitoring touchpoints

| Audit | What it catches | How to run |
|---|---|---|
| `scripts/audit_bronze.py` | Bronze schema drift, null symbols, source-tag distribution, adjustment status, live freshness | Daily cron / CI |
| `scripts/probe_provider_adjustment.py` | Provider API change in adjustment behavior | At onboarding + when bar_quality spikes |
| `CH ingestion_runs` (TA-5.7) | Which ingest jobs ran when, with what scope, success/fail | Query directly |
| `silver.bar_quality` (PLANNED TA-5.1) | Expected vs actual bars per day, provider disagreements | Daily review |

---

## 6. The ground-truth rule (architectural commitment)

From [silver_layer_plan.md §2.1](silver_layer_plan.md), restated
here so the rule travels with the ingestion architecture:

> **S3 silver is canonical. ClickHouse is a derived hot cache.
> Historical data (>48h old) NEVER enters CH directly from a
> provider — only via silver_to_ch_backfill.**

Two paths write to CH:
1. **Live stream** (Schwab WebSocket → CH live overlay zone,
   marked `source = "{provider}-stream"`).
2. **silver_to_ch_backfill** (planned TA-5.3 — silver historical
   → CH historical zone).

Plus one **bounded exception**: the tip-fill (Schwab REST for the
window between silver's last build and the live-stream's first
bar) writes to BOTH bronze AND CH because the data is near-live,
not historical archive (≤ 48h window).

Everything else (Polygon flat-files, Polygon REST historical,
Schwab REST historical) goes to **bronze only**. The default for
the Polygon bulk-backfill script is `--no-write-clickhouse`.

---

## 7. Per-provider summary

| Provider | Live? | Historical scope | Bronze table | Live source tag | REST source tag |
|---|---|---|---|---|---|
| Polygon (flat-files) | No | Whole market × 5-20y | `bronze.polygon_minute` | — | `polygon-flatfiles` |
| Polygon (REST aggs) | No | Whole market on demand | `bronze.polygon_minute` (rare) | — | `polygon-rest` (rare) |
| Polygon (corp-actions REST) | No | Whole market historical | `bronze.polygon_corp_actions` | — | (n/a; per-row `source_provider = "polygon"`) |
| Schwab (CHART_EQUITY stream) | **YES** | Seed universe live (~100) | `bronze.schwab_minute` (via TA-5.7) | `schwab-stream` | — |
| Schwab (REST `/pricehistory`) | No | Seed × 48d 1m + multi-yr 1d | `bronze.schwab_minute` | — | `schwab` |

Adjustment status (empirically verified 2026-05-17):
- **Polygon flat-files**: RAW (unadjusted)
- **Polygon REST `adjusted=false`**: RAW
- **Polygon REST `adjusted=true`**: SPLIT_ADJUSTED
- **Schwab REST `/pricehistory`**: SPLIT_ADJUSTED
- **Schwab CHART_EQUITY stream**: SPLIT_ADJUSTED (inferred from REST behavior)

Per-table constants in `app/services/bronze/schemas.py`:
- `BRONZE_POLYGON_MINUTE_ADJUSTMENT_STATUS = "raw"`
- `BRONZE_SCHWAB_MINUTE_ADJUSTMENT_STATUS = "split_adjusted"`

The silver OHLCV build (TA-5.1) will read these constants to
decide how to populate the `_raw` vs `_adj` columns per provider.
