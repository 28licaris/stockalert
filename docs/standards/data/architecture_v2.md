# StockAlert Data Architecture v2 — Production Grade

**Status:** PROPOSED — supersedes the medallion lock-in in
[`symbol_lifecycle.md`](symbol_lifecycle.md) once approved.
Read this before touching ingest, lake, or ML training code.

## TL;DR

Two tiers, three datasets, one canonical universe.

```
TIER 1 — LIVE (ClickHouse): user-facing charting, indicators, screener, sim.
TIER 2 — LAKE  (S3 + Iceberg): analytical / ML training workloads.

Datasets:
  data.polygon_raw       — whole-market 5y Polygon flat-files, RAW, immutable
  data.polygon_adjusted  — whole-market 5y, SPLIT-ADJUSTED (one-time build + corp-action updates)
  data.schwab_universe   — Schwab 1-minute bars, ALREADY ADJUSTED by Schwab, grows live
```

**Schwab data needs no adjustment math** — Schwab returns split-adjusted
bars natively. The corp-actions adjustment ETL applies only to the
Polygon historical archive (one-time + incremental on new corp actions).

**No "bronze → silver → derived CH" pipeline on the live path.**
Schwab REST + WS write directly to CH for charting. Schwab live also
flushes to S3 (Iceberg) for the ML training dataset to grow over time.

**Spark support is first-class** for batch ML jobs via Iceberg's
Spark integration. DuckDB serves single-user ad-hoc queries.

## Goals

| Goal | Met by |
|---|---|
| Live chart on add — chart-ready in ≤5s for new symbol | Schwab REST → CH direct write (48d 1-min + 20y daily) |
| Live chart latency — every zoom <200ms | CH SQL + on-the-fly resample |
| ML training — whole-market 5y of adjusted 1-min bars | `data.polygon_adjusted` (Iceberg) read by Spark / DuckDB |
| ML training — continuous universe data going forward | `data.schwab_universe` (Iceberg) grows via live stream |
| Cross-provider continuity for training | Schema-compatible Iceberg tables → `UNION ALL` joins natively |
| Production scale (terabytes, cluster compute) | Iceberg + Spark; partition-by-symbol-bucket for fast single-symbol scans |
| Reproducibility for ML | Iceberg snapshots; training jobs pin a snapshot_id |
| Provider independence | `DataProvider` abstraction; Schwab is plug-in not hard-coded |
| Failure isolation | Live tier survives lake outage; lake survives provider outage |
| Cost | $0 for Schwab API; one-time Polygon for historical; ~$5/mo S3 |

## Storage map

### Tier 1 — Live (ClickHouse)

| Table | Contents | Source | Adjusted? | Retention |
|---|---|---|---|---|
| `ohlcv_1m` | 1-minute bars for `stream_universe` | Schwab WS (live) + Schwab REST (on-add 48d) | yes (Schwab native) | full history per symbol from add-date forward |
| `ohlcv_daily` | Daily bars for `stream_universe` | Schwab REST on-add (20y) | yes (Schwab native) | full history per symbol from add-date forward |
| `stream_universe` | Canonical "what's actively streamed" | Cockpit Stream Service page | n/a | persistent |
| `watchlists`, `watchlist_members` | User-organizing labels | Cockpit | n/a | persistent |
| `signals`, `agent_runs`, `sim_trades`, ... | App state | App | n/a | persistent |

**ClickHouse is the only data store the live API reads from.** It is
re-buildable from the lake at any time (~1 hour for the universe).
No `ohlcv_5m` writes — chart resamples 1m → 5m/15m/30m/1h/4h via
`toStartOfInterval()` at query time.

### Tier 2 — Lake (S3 + Iceberg)

| Iceberg table | Contents | Source | Adjusted? | Partition | Sort |
|---|---|---|---|---|---|
| `data.polygon_raw` | Polygon flat-files, whole-market, every trading day | one-time bulk + optional periodic refresh | **no (raw)** | `bucket(symbol, 32), month(ts)` | `(symbol, ts)` |
| `data.polygon_adjusted` | Polygon, whole-market, split-adjusted | computed once from `polygon_raw + market_corp_actions`; incremental updates on new corp actions | **yes (computed)** | `bucket(symbol, 32), month(ts)` | `(symbol, ts)` |
| `data.schwab_universe` | Schwab live + REST tip-fill, universe only | live_lake_writer flushes CH; mirrors what CH stores | **yes (Schwab native — passthrough)** | `bucket(symbol, 16), month(ts)` | `(symbol, ts)` |
| `data.market_corp_actions` | Splits + dividends, whole-market | Polygon REST corp-actions ingest (weekly) | n/a | `month(ex_date)` | `(symbol, ex_date)` |

#### Why `bucket(symbol, N)` partitioning matters

Today's `bronze.polygon_minute` is partitioned by `month(ts)` only.
Single-symbol queries scan whole-market month files. **Adding symbol
bucketing slashes single-symbol scan cost by 32×.**

A single-symbol 5-year scan on the new layout:
- Old: 60 monthly Parquet files × ~5 GB each = ~300 GB scanned
- New: 60 monthly Parquet files × ~150 MB (one bucket) = ~9 GB scanned

This makes Spark / DuckDB queries on single symbols fast even on a
laptop. ML feature engineering becomes cheap.

#### Tiering layout in S3

```
s3://stockalert-lake/
├── data/
│   ├── polygon_raw/
│   │   ├── metadata/                     ← Iceberg snapshots
│   │   └── data/
│   │       ├── ts_month=2020-01/
│   │       │   ├── symbol_bucket=0/file-N.parquet
│   │       │   ├── symbol_bucket=1/file-N.parquet
│   │       │   ... (32 buckets)
│   │       ├── ts_month=2020-02/
│   │       ... (60 months × 32 buckets)
│   ├── polygon_adjusted/
│   │   ├── metadata/
│   │   └── data/ ... (same layout)
│   ├── schwab_universe/
│   │   ├── metadata/
│   │   └── data/ ... (16 buckets — smaller universe)
│   └── market_corp_actions/
│       ├── metadata/
│       └── data/
└── glacier/                              ← optional: raw flat-files >2y → Glacier Deep Archive
    └── polygon/{YYYY}/{MM}/{DD}.csv.gz
```

S3 storage class: Standard for ≤2y, Glacier Deep Archive for >2y raw.
Cost: ~$3-5/month for the whole lake at current scale.

### What's removed

| Removed | Reason | Replacement |
|---|---|---|
| `bronze.polygon_minute` | Renamed to `data.polygon_raw`, re-partitioned with symbol bucket | `data.polygon_raw` |
| `bronze.schwab_minute` | Schwab is already adjusted; merging into universe table | `data.schwab_universe` |
| `silver.ohlcv_1m` | Multi-source dedup not needed (Schwab adjusted natively + Polygon adjusted built separately) | `data.polygon_adjusted` (ML) + CH (live) |
| `silver_ohlcv_build` nightly job | Replaced by a one-time + incremental adjustment ETL | `polygon_adjustment_job` (weekly + on-corp-action-trigger) |
| `silver_to_ch_refresh` (deferred) | CH is rebuilt directly from Schwab REST on add | n/a |
| CH `ohlcv_5m` writes | Chart resamples 1m at query time | n/a |
| Schwab REST nightly | Schwab WS + on-add cover universe needs; no additional REST nightly required | n/a |

The medallion vocabulary (`bronze`, `silver`) is retired. Datasets are
named by **what they contain**, not by their position in a pipeline.

## Ingest paths

### Path A — Live charting (real-time, universe)

```
Schwab CHART_EQUITY WebSocket
        ▼
   bar_batcher (5s / 500 rows)
        ▼
   CH.ohlcv_1m  (source = "schwab-live")
        │
        │ live_lake_writer (every 5 min)
        ▼
   data.schwab_universe   (Iceberg)
```

- CH is the source of truth for live charting.
- Iceberg lake mirrors CH for ML training continuity.
- Both adjusted (Schwab native, no math).

### Path B — On-add fast path (single symbol)

```
POST /api/v1/stream {"symbol": "PG"}
        ▼
   stream_universe row written (CH)
        ▼
   schwab_provider.subscribe_bars(["PG"])      → CH.ohlcv_1m forward (live)
        ▼
   parallel:
     ├─ schwab_rest_pricehistory(PG, 48d × 1m)  → CH.ohlcv_1m       ~1-2s
     └─ schwab_rest_pricehistory(PG, 20y × 1d)  → CH.ohlcv_daily    ~1-2s

   Total wall-clock: ~3-5s. Chart usable at every zoom.
```

For 1-minute data deeper than 48 days for the new symbol: query
`data.polygon_adjusted` via DuckDB at chart render time (lazy, cached).
Most users never hit this — the 5y chart zoom uses daily candles.

### Path C — Polygon historical (one-time + incremental)

```
ONE-TIME BULK (already done):
   Polygon flat-files (5y whole-market)
        ▼
   data.polygon_raw   (Iceberg, RAW)

ONE-TIME ADJUSTMENT (post-bulk):
   data.polygon_raw + data.market_corp_actions
        ▼
   polygon_adjustment_job (Spark or local)
        ▼
   data.polygon_adjusted  (Iceberg, ADJUSTED, whole-market)

INCREMENTAL (when new corp actions land):
   Polygon REST corp-actions ingest (weekly)
        ▼
   data.market_corp_actions  (Iceberg, whole-market)
        ▼
   polygon_adjustment_job (only re-adjusts affected symbols)
        ▼
   data.polygon_adjusted  (updated for affected symbols)
```

The adjustment job is a Spark batch job (production) or local
Python script (dev). Runs:
- Once after the initial 5y bulk load (already-done bronze data).
- Weekly to incorporate new corp_actions.
- On-demand for individual symbols when a corp action is detected.

### Path D — Optional ongoing Polygon refresh

If the operator wants to keep `data.polygon_raw` growing (vs frozen
at the bulk load date), Polygon flat-files can re-enable nightly:

```
07:00 UTC  Polygon flat-files → data.polygon_raw (yesterday × whole-market)
                              → polygon_adjustment_job triggered
                              → data.polygon_adjusted (yesterday × whole-market)
```

Disabled by default. Re-enable when:
- Live charting needs whole-market context (e.g. sector rotation analysis).
- ML training wants to keep up with the latest universe expansion.

## Read paths

### Live API (FastAPI) — reads CH only

```python
# routes_market.py
bars = await asyncio.to_thread(
    queries.list_bars_resampled,
    symbol, interval, start, end, limit,
    source_table="ohlcv_1m",
)
```

CH SQL with on-the-fly `toStartOfInterval` resampling. <200ms for any
zoom. No Iceberg dependency on the live path.

### Operator ad-hoc — DuckDB

```python
import duckdb

# Whole-market historical
df = duckdb.sql("""
  SELECT * FROM iceberg_scan('s3://stockalert-lake/data/polygon_adjusted/')
  WHERE symbol = 'AAPL' AND timestamp BETWEEN '2020-01-01' AND '2024-12-31'
""").df()

# Cross-provider continuity for ML training
df = duckdb.sql("""
  SELECT symbol, timestamp, open, high, low, close, volume, 'polygon' AS source
  FROM iceberg_scan('s3://stockalert-lake/data/polygon_adjusted/')
  WHERE symbol = 'AAPL' AND timestamp < '2025-05-01'

  UNION ALL

  SELECT symbol, timestamp, open, high, low, close, volume, 'schwab' AS source
  FROM iceberg_scan('s3://stockalert-lake/data/schwab_universe/')
  WHERE symbol = 'AAPL' AND timestamp >= '2025-05-01'
  ORDER BY timestamp
""").df()
```

DuckDB reads Iceberg directly. Symbol-bucket partitioning makes
single-symbol queries fast (~1-3s for 5y of 1-min data).

### ML training — Spark

For training jobs that need cluster compute, Spark reads the same
Iceberg tables.

```python
from pyspark.sql import SparkSession

spark = (SparkSession.builder
    .appName("ml_training_features")
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.lake.type", "glue")
    .config("spark.sql.catalog.lake.warehouse", "s3://stockalert-lake/data")
    .getOrCreate())

# Snapshot-pinned for reproducibility (train v1 always reads the same data)
df = spark.sql("""
  SELECT * FROM lake.polygon_adjusted
  VERSION AS OF 1234567890   -- Iceberg snapshot id
  WHERE timestamp BETWEEN '2020-01-01' AND '2024-12-31'
""")

# Feature engineering
features = df.groupBy("symbol", window("timestamp", "1 day")) \
    .agg(...)
```

Spark + Iceberg natively supports time-travel queries (`VERSION AS OF`)
and incremental reads (`CHANGED ROWS`). Training reproducibility is
preserved by pinning snapshot IDs in the training pipeline config.

### Spark cluster options

| Option | Cost | Setup | When |
|---|---|---|---|
| Local Spark | $0 | `pip install pyspark` | Dev, small datasets, single-node |
| AWS EMR Serverless | pay-per-job (~$0.30/hour DBU) | Glue catalog already configured | Production batch jobs; no cluster mgmt |
| AWS EMR on EC2 | EC2 hourly | Manual cluster lifecycle | Long-running ETL pipelines |
| Databricks | per-DBU pricing | Connect to Glue catalog | If team already uses Databricks |

**Default: local Spark for dev + EMR Serverless for production training.**
Iceberg's standard format means jobs are portable across these without
code changes.

## Schema

All four datasets share the **same canonical OHLCV schema** so
joins/unions are trivial.

```sql
CREATE TABLE data.polygon_adjusted (
    symbol      STRING NOT NULL,
    timestamp   TIMESTAMP NOT NULL,         -- UTC
    open        DOUBLE NOT NULL,
    high        DOUBLE NOT NULL,
    low         DOUBLE NOT NULL,
    close       DOUBLE NOT NULL,
    volume      DOUBLE NOT NULL,
    vwap        DOUBLE,
    trade_count INT,
    source      STRING NOT NULL,            -- 'polygon-adjusted' | 'schwab-live' | 'schwab-rest-pricehistory'
    adj_factor  DOUBLE NOT NULL DEFAULT 1.0 -- cumulative split factor applied (1.0 = no splits)
)
PARTITIONED BY (
    bucket(32, symbol),
    month(timestamp)
)
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'zstd',
    'write.distribution-mode' = 'hash',
    'write.upsert.mode' = 'merge-on-read'
);
```

Same for `data.schwab_universe` (with `bucket(16, symbol)`) and
`data.polygon_raw` (without `adj_factor`).

`source` tag lets queries filter / dedupe across datasets. Iceberg
upsert semantics ensure idempotent re-writes.

## Live API contract (unchanged from cockpit's perspective)

The cockpit reads exactly the same FastAPI endpoints as today:

| Endpoint | Source |
|---|---|
| `GET /api/v1/health/services` | Subsystem probes (CH ping, Schwab token, etc.) |
| `GET /api/v1/stream/status` | `stream_service.status()` |
| `GET /api/v1/jobs` | `job_registry.list()` |
| `GET /api/v1/symbol/.../bars` | `BarReader.get_bars_for_chart()` → CH |
| `GET /api/v1/instruments/search` | Schwab provider |
| `POST /api/v1/stream {"symbol"}` | `stream_service.add()` → CH.ohlcv_1m + CH.ohlcv_daily |
| `GET /api/v1/lake/bars` | DuckDB → `data.polygon_adjusted` / `data.schwab_universe` |

No cockpit changes needed. The architecture refactor is purely
backend.

## Provider abstraction (forward-compatible)

```
app/providers/
├── base.py                    DataProvider Protocol
├── schwab_provider.py         Today's primary
├── polygon_flatfiles.py       For lake bulk loads
├── polygon_provider.py        For corp-actions REST (whole-market)
├── alpaca_provider.py         (future) — pluggable
└── yfinance_provider.py       (future) — fallback for daily-only
```

Live tier registers ONE provider for streaming via `DATA_PROVIDER` env.
Lake operations are provider-specific tools (no abstraction needed —
they're batch ETL jobs, not part of the streaming hot path).

## Failure isolation

| Failure | Effect on live tier | Effect on lake/ML |
|---|---|---|
| Schwab outage | Live ticks stop; chart shows last bar + "stale" badge. No new on-add for symbols. | Lake unaffected; ML reads existing snapshots. |
| Schwab token expired | Same as above; OAuth refresh required (operator). | Lake unaffected. |
| Polygon subscription ends | Live tier unaffected (Schwab only). | `data.polygon_raw` frozen at last refresh date. Existing snapshots queryable forever. `data.polygon_adjusted` stops growing whole-market. Schwab universe keeps growing. |
| CH down | Live API returns 503. | Lake unaffected. |
| CH corrupted | Restore from `data.schwab_universe` Iceberg snapshot (~1 hour for universe). | n/a |
| S3 region outage | Live tier unaffected (CH local). | ML / DuckDB queries fail until region returns. |
| Iceberg catalog (Glue) outage | Live tier unaffected. | ML / DuckDB writes fail; reads via direct S3 still work for already-known snapshots. |
| Bad corp-action data | New silver build with corp-action-dirty rebuild fixes the affected symbol(s) on next adjustment-job run. | Same. |

**No single component is on the critical path for both live and ML
workloads.** Each tier's failure is contained.

## Migration from v1 (current state)

The current state has:
- `bronze.polygon_minute` (Iceberg, whole-market 5y, raw)
- `bronze.schwab_minute` (Iceberg, universe, raw)
- `silver.ohlcv_1m` (Iceberg, universe, adjusted from bronze + corp_actions)
- `bronze.polygon_corp_actions` (Iceberg, whole-market)
- CH `ohlcv_1m`, `ohlcv_5m`, `ohlcv_daily` (universe, derived)

The migration replaces silver-on-the-live-path with direct Schwab
writes, renames bronze → data., and computes polygon_adjusted as a
one-time job.

### Migration phases

**Phase 1 — Add the new datasets (additive, no risk).**
1. Create Iceberg tables `data.polygon_raw`, `data.polygon_adjusted`,
   `data.schwab_universe`, `data.market_corp_actions` with the new
   partition spec (symbol bucket + month).
2. Run a one-time copy from `bronze.polygon_minute` →
   `data.polygon_raw` (rewrites files with new partitioning; ~30 min
   for 120 GB on EMR or local Spark).
3. Run a one-time copy `bronze.polygon_corp_actions` →
   `data.market_corp_actions` (~5 min).
4. Run `polygon_adjustment_job` against `data.polygon_raw +
   data.market_corp_actions` → `data.polygon_adjusted` (one-time
   build, ~1-2 hours on EMR for whole market).

**Phase 2 — Switch the live writers (live tier only).**
5. Reconfigure `live_lake_writer` to write to `data.schwab_universe`
   (was `bronze.schwab_minute`).
6. Verify: live ticks land in CH (unchanged) + `data.schwab_universe`
   (new).
7. Stop writing to `bronze.schwab_minute` (keep readable for 30 days
   as backup).

**Phase 3 — Switch the on-add hot path.**
8. Stream warmup chain: replace silver_build call with direct Schwab
   REST writes to CH (1-min × 48d + daily × 20y).
9. Live verification — re-run latency gate. Expect <5s.
10. Mark `silver_ohlcv_build` nightly job DISABLED. Mark `silver.ohlcv_1m`
    READ-ONLY.

**Phase 4 — Update reads.**
11. Update `BarReader.get_bars_for_chart` — confirm 5m/daily fallback
    paths still resample from `CH.ohlcv_1m` cleanly (already
    implemented; just confirm no regression).
12. Add lake-read endpoint `GET /api/v1/lake/bars?symbol=...&since=...`
    that queries `data.polygon_adjusted` / `data.schwab_universe`
    via DuckDB. Used by chart when zooming beyond CH's window OR
    by operator/MCP tools.

**Phase 5 — Decommission.**
13. After 30 days of v2 in production with no regressions:
    - `DROP TABLE bronze.schwab_minute`
    - `DROP TABLE silver.ohlcv_1m`
    - `DROP TABLE bronze.polygon_minute` (data already copied to
      `data.polygon_raw`)
14. Update `docs/standards/data/symbol_lifecycle.md` to reference
    architecture_v2.md as authoritative.

Each phase is independently committable + reversible. Phases 1-2
are additive. Phase 3 is the only point of behavior change; the
on-add path falls back to silver_build if Schwab REST fails (defensive
during cutover, removed after).

### Commits

| Phase | Commit | Title |
|---|---|---|
| 1 | CV1 | `feat(lake): create v2 Iceberg tables (data.*) with bucket partitioning` |
| 1 | CV2 | `feat(lake): one-time copy bronze.polygon_minute → data.polygon_raw` |
| 1 | CV3 | `feat(lake): one-time polygon_adjustment_job → data.polygon_adjusted` |
| 2 | CV4 | `refactor(ingest): live_lake_writer writes to data.schwab_universe` |
| 3 | CV5 | `feat(stream): on-add warmup direct Schwab REST → CH (no silver build)` |
| 3 | CV6 | `test(integration): latency gate <5s` |
| 4 | CV7 | `feat(api): /api/v1/lake/bars endpoint via DuckDB` |
| 5 | CV8 | `chore: drop bronze.* and silver.* tables (decommissioned)` |

## Operational tooling

### `polygon_adjustment_job`

A Spark batch job (or local PySpark script for dev) that produces
`data.polygon_adjusted` from `data.polygon_raw` + `data.market_corp_actions`.

```
Inputs:
  --symbols all | <csv> | universe   (universe = stream_universe)
  --since-date <YYYY-MM-DD>          (default: full history)
  --until-date <YYYY-MM-DD>          (default: yesterday)
  --mode full | incremental          (incremental = symbols dirty since last run)

Outputs:
  s3://stockalert-lake/data/polygon_adjusted/...
  ingestion_runs row recording the snapshot_id

Default schedule: weekly via EMR Serverless (CodeBuild trigger).
On-demand: operator invokes via `scripts/run_polygon_adjustment.py`.
```

### `lake_archive_job`

Periodic CH → S3 Iceberg flush. Replaces `live_lake_writer`'s direct
Iceberg writes with a periodic batch (cheaper, fewer small files).

```
Schedule: every 1 hour
Action:   reads CH.ohlcv_1m where timestamp > watermark
          appends to data.schwab_universe
          advances watermark
Cost:     ~30s per run; replaces 12 5-min live_lake_writer runs
```

Iceberg's `merge-on-read` upsert handles re-runs idempotently.

## Storage cost estimate

| Layer | Size | Cost / month |
|---|---|---|
| CH live tier (108 syms × 5y × 1m + daily) | ~10 GB | $0 (self-hosted) |
| `data.polygon_raw` (12k syms × 5y × 1m) | ~120 GB | $2.80 (S3 Standard) |
| `data.polygon_adjusted` (12k syms × 5y × 1m + adj_factor) | ~150 GB | $3.50 |
| `data.schwab_universe` (108 syms × growing) | ~5 GB at year 1 | $0.10 |
| `data.market_corp_actions` | ~50 MB | <$0.01 |
| Iceberg metadata (Glue catalog) | minimal | $0.05 |
| Glacier Deep Archive (>2y raw) | up to ~60 GB later | $0.06 |

**Total recurring cost: ~$7/month** for the whole lake.

Plus: Polygon flat-files subscription if you choose to refresh
periodically (~$200/mo).

## Concrete checklist before "production grade"

| Item | Status |
|---|---|
| Schema-versioned Iceberg tables with partition spec | TBD (Phase 1) |
| Snapshot pinning for ML reproducibility | Built-in to Iceberg (free) |
| Compaction policy on `data.*` tables | TBD — schedule weekly compaction via EMR |
| Backup / restore procedure documented | TBD — `data.*` is the backup |
| Disaster recovery: CH rebuild from lake script | TBD — 1-shot script reading `data.schwab_universe` → CH |
| Audit trail for every write (`ingestion_runs`) | EXISTS — extend to all v2 ingest paths |
| Cost monitoring (S3 Storage Lens, CH disk usage) | TBD — operator dashboard |
| ML training Spark job templates | TBD — `scripts/spark/` directory |
| Provider abstraction (multi-provider live) | EXISTS — `DataProvider` Protocol |
| Failure isolation tests (live ≠ lake) | TBD — integration tests |

## What this doc replaces

| Old doc | New status |
|---|---|
| [`docs/streaming_universe_model.md`](../../streaming_universe_model.md) | SUPERSEDED |
| [`docs/standards/data/symbol_lifecycle.md`](symbol_lifecycle.md) | SUPERSEDED on architecture; quick-path retained as runtime spec |
| [`docs/data_platform_plan.md`](../../data_platform_plan.md) | Phase 1 medallion language retired; this doc is the new platform plan |
| [`docs/standards/data/lean_silver.md`](lean_silver.md) | RETIRED (silver layer removed) |
| [`docs/standards/data/bronze_idempotency.md`](bronze_idempotency.md) | RETIRED (bronze layer renamed; idempotency now lives in Iceberg merge-on-read) |

## Approval gates (before any code changes)

1. **Naming**: `data.polygon_raw` / `data.polygon_adjusted` /
   `data.schwab_universe` / `data.market_corp_actions` — OK?
   Alternative: `lake.*` or `equities.*` as the catalog prefix.

2. **Schema decision**: include `adj_factor` (cumulative split factor)
   in adjusted tables? Useful for back-computing raw prices in ML
   features. Adds one DOUBLE column. **My pick: yes.**

3. **Partition strategy**: `bucket(32, symbol), month(ts)` for whole-
   market tables, `bucket(16, symbol), month(ts)` for universe. **My
   pick: yes.** Tunable later via Iceberg's partition evolution.

4. **Compaction cadence**: weekly compaction via EMR Serverless to
   keep file count bounded (~500 files per dataset target). **My pick:
   yes, scheduled job in Phase 1.**

5. **Compute platform for the adjustment ETL + Spark batch jobs**:
   EMR Serverless (recommended; pay-per-job, no cluster management)
   vs Databricks vs local Spark vs DuckDB-only (skip Spark for now,
   add when needed). **My pick: start with DuckDB for dev + EMR
   Serverless for production. No infra to manage day-to-day.**

6. **Migration risk tolerance**: each phase commits + verifies; the
   live tier stays on the current path until Phase 3 cuts over;
   Phase 5 decommissions only after 30 days of clean v2 operation.
   **OK?**

7. **Lake-read endpoint** (`/api/v1/lake/bars`): worth building for
   ad-hoc deep-history queries from the cockpit, or operator-only via
   DuckDB CLI? **My pick: build the endpoint; the cockpit will need
   it eventually for chart zoom-out beyond CH retention.**

Approve points 1-7 (or amend) and we move into Phase 1 implementation.
