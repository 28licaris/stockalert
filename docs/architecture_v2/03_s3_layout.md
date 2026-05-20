# 03 — S3 Layout & Partition Strategy

## Bucket structure

```
s3://stockalert-lake/
│
├── data/                                  ← Iceberg warehouse root
│   │
│   ├── polygon_raw/                       ← Whole-market 5y, RAW unadjusted
│   │   ├── metadata/
│   │   │   ├── v1.metadata.json           ← snapshot 1 (initial bulk load)
│   │   │   ├── v2.metadata.json           ← snapshot 2 (Polygon Jan 2025 nightly)
│   │   │   ├── ... (one per write commit)
│   │   │   ├── snap-{snapshot_id}.avro    ← manifest list per snapshot
│   │   │   └── {manifest_id}.avro         ← manifests (point to data files)
│   │   │
│   │   └── data/
│   │       ├── timestamp_month=2020-01/
│   │       │   ├── symbol_bucket=0/       ← 32 buckets (whole-market)
│   │       │   │   ├── 00000-0-abc...parquet      (~150 MB)
│   │       │   │   └── 00001-0-def...parquet
│   │       │   ├── symbol_bucket=1/
│   │       │   │   └── ...
│   │       │   ... (32 buckets per month)
│   │       │
│   │       ├── timestamp_month=2020-02/
│   │       │   └── ...
│   │       │
│   │       ... (60 months × 32 buckets ≈ 1920 dirs, ~4000 Parquet files)
│   │
│   ├── polygon_adjusted/                  ← Same layout, with adj_factor column
│   │   ├── metadata/
│   │   └── data/
│   │       └── timestamp_month=YYYY-MM/symbol_bucket=N/...parquet
│   │
│   ├── schwab_universe/                   ← Universe live, ALREADY adjusted
│   │   ├── metadata/
│   │   └── data/
│   │       ├── timestamp_month=2025-05/
│   │       │   ├── symbol_bucket=0/       ← 16 buckets (smaller universe)
│   │       │   │   └── ...parquet
│   │       │   ... (16 buckets per month)
│   │       └── ... (one timestamp_month per current month + retention window)
│   │
│   └── market_corp_actions/               ← Splits + dividends, whole-market
│       ├── metadata/
│       └── data/
│           ├── ex_date_month=2024-12/
│           │   └── 00000-0-...parquet     (~5 MB per month)
│           └── ...
│
├── raw/                                   ← Original provider files (DR backup)
│   └── polygon/
│       └── us_stocks_sip/
│           └── minute_aggs_v1/
│               ├── 2020/01/2020-01-02.csv.gz   ← actual flat-files
│               ├── 2020/01/2020-01-03.csv.gz
│               └── ... (Polygon's native flat-file format)
│
└── glacier/                               ← S3 Lifecycle moves >2y here automatically
    └── polygon/
        └── ...
```

### Bucket naming convention

| Bucket | Purpose | Storage class |
|---|---|---|
| `stockalert-lake` | Primary data lake + Iceberg metadata | S3 Standard |
| `stockalert-lake/glacier/` | Old raw files (>2y) | S3 Glacier Deep Archive (via lifecycle policy) |
| `stockalert-models` | ML model artifacts + training feature outputs | S3 Standard |
| `stockalert-features` | Materialized feature views | S3 Standard |
| `stockalert-code` | Spark JARs, scripts deployed to EMR | S3 Standard |

Lifecycle policy on `stockalert-lake/raw/polygon/`:
- 0-2 years: Standard
- 2+ years: Glacier Deep Archive (rarely accessed; cheap)

## Partition strategy — why `bucket(N, symbol), month(ts)`

The single most important design decision in the lake layout. v1's
`bronze.polygon_minute` was partitioned by `month(timestamp)` only —
each monthly Parquet file contained the **whole market** (~5 GB). A
single-symbol query had to scan all 60 monthly files = ~300 GB.

v2's `bucket(N, symbol)` hashes each symbol into N buckets. Each
(symbol) always lands in the same bucket. **Single-symbol queries
read 1/N of each month's data instead of all of it.**

### Concrete numbers

For `equities.polygon_raw` at 12,000 symbols × 5 years × 1-minute:

| Layout | Files / month | File size | Total scan for 5y of 1 symbol |
|---|---|---|---|
| v1 (month only) | 1 | ~5 GB | 60 × 5 GB = **~300 GB** ⇒ minutes |
| v2 (month + bucket(32)) | 32 | ~150 MB | 60 × 150 MB = **~9 GB** ⇒ **~3-5 seconds** |

### Choosing N (bucket count)

Rule of thumb: target ~5-10 symbols per bucket so per-bucket file
sizes hit Iceberg's recommended ~128 MB target.

| Dataset | Symbols | Bucket count N | Symbols per bucket |
|---|---|---|---|
| `polygon_raw` / `polygon_adjusted` | ~12,000 | 32 | ~375 |
| `schwab_universe` | ~250 (Top-N by 30d ADV — Gate 13) | 16 | ~16 |
| `market_corp_actions` | low row volume | no bucketing | n/a |

Schwab universe gets 16 buckets — small data; over-bucketing creates
tiny files which Iceberg compacts but generates metadata overhead.

### Bucket count is evolvable

If we ever decide 32 isn't enough (e.g. add micro-cap coverage and
symbol count grows to 50k), Iceberg supports partition evolution:

```sql
ALTER TABLE lake.equities.polygon_raw
SET PARTITION SPEC (bucket(64, symbol), month(timestamp));
```

Existing files keep their old partition; new writes use the new spec.
Iceberg's planner handles both transparently. No data migration
needed.

## File sizing

| Setting | Value | Rationale |
|---|---|---|
| `write.target-file-size-bytes` | 128 MB | Iceberg recommended; balances file count vs read efficiency |
| `write.parquet.row-group-size-bytes` | 16 MB | Row group stats give fine-grained skip during scans |
| `write.parquet.compression-codec` | `zstd` (level 3) | ~30% smaller than snappy at acceptable encode cost |
| `write.distribution-mode` | `hash` (by partition key) | Avoids file-per-task explosion in Spark writes |

### Expected on-disk sizes

| Dataset | Rows | Uncompressed | Zstd Parquet | File count |
|---|---|---|---|---|
| `polygon_raw` | ~5B (12k × 5y × ~390 bars/day × 252 trading days) | ~500 GB | **~120 GB** | ~4,000 files |
| `polygon_adjusted` | same as raw + adj_factor column | ~520 GB | **~140 GB** | ~4,000 files |
| `schwab_universe` | grows ~5M rows/day | ~0.5 GB/year initial | **~5 GB/year** | ~200 files/year |
| `market_corp_actions` | ~1.5M rows total | ~50 MB | **~10 MB** | <50 files |

## Compaction (file-size hygiene)

Iceberg writes append new files for each commit. Small batches
(e.g. `lake_archive_job` running hourly) produce small files
(~30 MB), which is fine short-term but degrades read performance
over months.

**Schedule weekly compaction via EMR Serverless** using Iceberg's
built-in procedure:

```sql
CALL lake.system.rewrite_data_files(
    table => 'lake.equities.schwab_universe',
    options => map('target-file-size-bytes', '134217728', 'min-file-size-bytes', '67108864')
);
```

Same for the other tables (less frequent; `polygon_raw` is mostly
static and `polygon_adjusted` rewrites only on corp-action changes).

Compaction is non-blocking — readers see the old files until the
commit succeeds, then atomically switch.

## Iceberg snapshot retention

Iceberg keeps every write commit as a separate snapshot. Default
retention is unlimited, which grows metadata over time. Configure:

```sql
ALTER TABLE lake.equities.polygon_raw
SET TBLPROPERTIES (
    'history.expire.min-snapshots-to-keep' = '20',
    'history.expire.max-snapshot-age-ms' = '7776000000'  -- 90 days
);
```

Keeps the last 90 days OR last 20 snapshots, whichever is more.
ML training pipelines pin specific snapshots; those stay protected.

Older snapshots are expired by a periodic maintenance call:

```sql
CALL lake.system.expire_snapshots('lake.equities.polygon_raw');
```

Run weekly via EMR Serverless (same cron as compaction).

## Storage cost estimate

| Layer | Size | Cost / month |
|---|---|---|
| `equities.polygon_raw` | ~120 GB | $2.80 (S3 Standard @ $0.023/GB) |
| `equities.polygon_adjusted` | ~140 GB | $3.30 |
| `equities.schwab_universe` | ~5 GB → ~50 GB at 10y | $0.10 → $1.15 |
| `equities.market_corp_actions` | ~10 MB | <$0.01 |
| Iceberg metadata (across all 4 tables) | <500 MB | $0.01 |
| `raw/polygon/...` (CSV.gz flat-files) | ~300 GB | $7.00 (Standard) or $0.30 (Glacier Deep Archive) |
| Glue catalog | 4 tables × ~100 commits/year | $0.05 |

**Recurring cost: ~$7-10 / month** for the whole lake.

## Direct S3 reads vs Iceberg catalog

Iceberg's metadata layer is small (< 1% of data size) but is
**required** for:
- Snapshot pinning (`VERSION AS OF`)
- Transactional writes (atomic appends)
- Schema evolution
- Hidden partition pruning (Spark figures out which files to read)

If the Glue catalog is unavailable, Spark + DuckDB can still read
the underlying Parquet files directly:

```python
duckdb.sql("""
    SELECT * FROM read_parquet(
        's3://stockalert-lake/equities/polygon_raw/data/**/*.parquet',
        hive_partitioning = 1
    )
    WHERE symbol = 'AAPL'
""")
```

Less performant (no Iceberg pruning, no schema validation), but a
viable fallback for emergency reads during catalog outages.

## See also

- [02_schema.md](02_schema.md) — DDL for these tables
- [04_spark.md](04_spark.md) — how Spark / DuckDB read this layout
- [07_runbook.md](07_runbook.md) — compaction + snapshot cleanup operator procedures
