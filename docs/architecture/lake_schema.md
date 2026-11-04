# Lake schema, DDL & S3 layout

Canonical reference for the Iceberg lake: column contracts, table DDL,
partitioning, file layout and maintenance. The system map is in
[`README.md`](README.md).

All bar datasets share the **same canonical OHLCV schema** so joins and unions
across providers are trivial.

---

## 1. Canonical OHLCV columns

| Column | Type | Required | Notes |
|---|---|---|---|
| `symbol` | STRING | yes | Uppercase, normalized (`watchlist_repo.normalize_member_symbol`) |
| `timestamp` | TIMESTAMP (μs UTC) | yes | UTC always. Naive timestamps must be normalized at write time |
| `open` | DOUBLE | yes | First trade of the bar |
| `high` | DOUBLE | yes | Max of the bar |
| `low` | DOUBLE | yes | Min of the bar |
| `close` | DOUBLE | yes | Last trade of the bar |
| `volume` | DOUBLE | yes | DOUBLE (not BIGINT) so split-factor multiplication stays exact |
| `vwap` | DOUBLE | no | Volume-weighted average price; 0/null if the provider omits it |
| `trade_count` | INT | no | 0/null if not supplied |
| `source` | STRING | yes | Origin tag (below) |
| `adj_factor` | DOUBLE | pre-adjusted tables | Cumulative split factor on this row. `1.0` = nothing to adjust |

### `source` tag conventions

| Value | Where it lives | Meaning |
|---|---|---|
| `"polygon-raw"` | `equities.polygon_raw`, `polygon_daily_raw` | Polygon flat-files / grouped-daily, untouched |
| `"schwab-live"` | `equities.schwab_universe`, `CH.ohlcv_1m` | Schwab `CHART_EQUITY` WebSocket |
| `"schwab-rest-pricehistory"` | `equities.schwab_universe`, `CH.ohlcv_1m` | Schwab REST tip-fill (on-add) |
| `"{provider}-stream"` | `CH.ohlcv_1m`, `futures_ohlcv_1m` | Live batcher writes |

### Why `adj_factor` is a column

For ML training where features depend on price *level*, you need to know what
scale a price is on:

```python
raw_close = adjusted_close * adj_factor
```

Without it, two symbols at the same nominal price can mean very different
things — one may have had a 10:1 split. `adj_factor = 1.0` on
`schwab_universe` because Schwab adjusts at the API and never exposes the
pre-split price. On futures `polygon_continuous`, `adj_factor` carries the
ratio back-adjustment and is `1.0` on the front segment, so multiplying
recovers true contract prices.

---

## 2. Split adjustment — the math

Adjustment is computed **at read time** (`app/services/equities/adjust.py`),
not materialized. For a symbol with splits at `ex_date_1 < ex_date_2 < …`, the
cumulative factor applied to a bar at time `t` is:

```
F(symbol, t) = ∏ split_ratio_i   for all splits where ex_date_i > t
```

— the product of all **future** split ratios. Then:

```
adjusted_open   = raw_open   / F
adjusted_high   = raw_high   / F
adjusted_low    = raw_low    / F
adjusted_close  = raw_close  / F
adjusted_volume = raw_volume * F
adj_factor      = F
```

Prices divide, volume multiplies.

### Example — AAPL's 4-for-1 split on 2020-08-31

| Bar timestamp | Raw close | F | Adjusted close |
|---|---|---|---|
| 2020-08-30 16:00 | $500 | 4.0 | $125 |
| 2020-08-31 09:30 | $129 | 1.0 | $129 |
| 2024-06-15 10:00 | $216 | 1.0 | $216 |

A chart spanning 2020 + 2024 shows continuous prices with no split cliff.

Splits are read from **`equities.market_splits`**, not `market_corp_actions` —
it is splits-only (~27k rows) and symbol-sorted, so a per-symbol lookup is
instant instead of scanning dividends. Full rationale:
[`data/market_splits.md`](../data/market_splits.md) and
[`data/adjusted_storage.md`](../data/adjusted_storage.md).

---

## 3. Table DDL

Tables are created by the `ensure_*()` helpers in
`app/services/{equities,futures,options}/tables.py`; schemas live in each
module's `schemas.py`. The DDL below is the equivalent SQL.

```sql
-- equities.polygon_raw — whole-market minute history, RAW unadjusted
CREATE TABLE lake.equities.polygon_raw (
    symbol      STRING NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    open        DOUBLE NOT NULL,
    high        DOUBLE NOT NULL,
    low         DOUBLE NOT NULL,
    close       DOUBLE NOT NULL,
    volume      DOUBLE NOT NULL,
    vwap        DOUBLE,
    trade_count INT,
    source      STRING NOT NULL
)
PARTITIONED BY (bucket(32, symbol), month(timestamp));

-- equities.schwab_universe — live + REST tip, pre-adjusted
CREATE TABLE lake.equities.schwab_universe (
    ...same columns...,
    adj_factor  DOUBLE NOT NULL   -- always 1.0
)
PARTITIONED BY (bucket(16, symbol), month(timestamp));

-- equities.market_splits — the read-time adjustment source
CREATE TABLE lake.equities.market_splits (
    symbol        STRING NOT NULL,
    ex_date       DATE NOT NULL,
    split_from    DOUBLE NOT NULL,
    split_to      DOUBLE NOT NULL
)
PARTITIONED BY (month(ex_date));
```

| Table | Partition spec | Why |
|---|---|---|
| `equities.polygon_raw` | `bucket(32, symbol), month(timestamp)` | ~12k symbols; single-symbol reads touch 1/32 of each month |
| `equities.polygon_daily_raw` | `month(timestamp)` | Daily volume is small; bucketing would make tiny files |
| `equities.schwab_universe` | `bucket(16, symbol), month(timestamp)` | ~250-symbol universe; 16 buckets avoids tiny files |
| `equities.market_corp_actions` | `month(ex_date)` | Low row volume |
| `equities.market_splits` | `month(ex_date)` | ~27k rows; symbol-sorted for instant lookup |
| `futures.schwab_futures` | `month(timestamp)` | Few roots, recent window only |
| `futures.polygon_raw` | `bucket(N, symbol), month(timestamp)` | Every outright contract |
| `futures.polygon_continuous` | `month(timestamp)` | ~37 roots |
| `options.schwab_chain_*` | snapshot/underlying keyed | See [`data/options_ingestion.md`](../data/options_ingestion.md) |

---

## 4. Partition strategy — why `bucket(N, symbol), month(ts)`

The single most important layout decision. The retired v1
`bronze.polygon_minute` partitioned by `month(timestamp)` only, so each
monthly Parquet file held the **whole market** (~5 GB) and a single-symbol
query had to scan all 60 monthly files.

`bucket(N, symbol)` hashes each symbol into a fixed bucket, so a single-symbol
query reads **1/N of each month** instead of all of it.

For `equities.polygon_raw` at 12,000 symbols × 5 years × 1-minute:

| Layout | Files/month | File size | Scan for 5y of one symbol |
|---|---|---|---|
| month only | 1 | ~5 GB | 60 × 5 GB = **~300 GB** ⇒ minutes |
| month + `bucket(32)` | 32 | ~150 MB | 60 × 150 MB = **~9 GB** ⇒ **~3-5 s** |

**Choosing N:** target file sizes near Iceberg's ~128 MB recommendation.
Over-bucketing a small dataset creates tiny files and metadata overhead —
hence 16 for `schwab_universe` and none for the corp-action tables.

**N is evolvable.** Iceberg supports partition evolution:

```sql
ALTER TABLE lake.equities.polygon_raw
SET PARTITION SPEC (bucket(64, symbol), month(timestamp));
```

Existing files keep the old spec, new writes use the new one, and the planner
handles both. No data migration.

---

## 5. S3 layout

```
s3://{STOCK_LAKE_BUCKET}/
├── iceberg/                        ← warehouse root (ICEBERG_WAREHOUSE_PREFIX)
│   ├── equities/                   ← Glue database
│   │   ├── polygon_raw/
│   │   │   ├── metadata/           ← vN.metadata.json, snap-*.avro, manifests
│   │   │   └── data/
│   │   │       └── ts_month=2020-01/symbol_bucket=0/00000-0-….parquet
│   │   ├── polygon_daily_raw/
│   │   ├── schwab_universe/        ← ts_month=…/symbol_bucket=… (16 buckets)
│   │   ├── schwab_daily_raw/
│   │   ├── market_corp_actions/    ← ex_month=YYYY-MM/
│   │   └── market_splits/
│   ├── futures/                    ← schwab_futures, schwab_futures_daily,
│   │                                 polygon_raw, polygon_continuous
│   └── options/                    ← schwab_chain_raw, schwab_chain_contracts,
│                                     schwab_expirations, gamma_exposure_snapshots
└── polygon_flatfiles_mirror/       ← byte-for-byte vendor archive (futures)
```

Table location format:
`s3://{STOCK_LAKE_BUCKET}/{ICEBERG_WAREHOUSE_PREFIX}/{glue_database}/{table}/`
— defaults to `s3://{bucket}/iceberg/equities/<table>/`.

Partition directory names (`symbol_bucket=N`, `ts_month=YYYY-MM`,
`ex_month=YYYY-MM`) are what PyIceberg's `BucketTransform` / `MonthTransform`
emit; covered by unit tests in
`app/services/equities/tests/test_equities_schemas.py`.

> **Credentials:** lake/S3 access requires `AWS_PROFILE=stock-lake`. A blank
> ambient `AWS_PROFILE` makes every lake call hang ~45-90s; `config.py`'s
> `_normalize_aws_env` guards against this.

### File sizing

| Setting | Value | Rationale |
|---|---|---|
| `write.target-file-size-bytes` | 128 MB | Iceberg recommended; balances file count vs read efficiency |
| `write.parquet.row-group-size-bytes` | 16 MB | Row-group stats give fine-grained scan skipping |
| `write.parquet.compression-codec` | `zstd` (level 3) | ~30% smaller than snappy at acceptable encode cost |

> **Multipart write gotcha:** PyIceberg large/multipart Parquet writes fail
> with `BadDigest` under S3's CRC64NVME checksums. Set
> `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` **before any AWS client
> initialization**. Small writes pass and full runs fail, so this surfaces late.

---

## 6. Maintenance

Bronze appends, silver dedups. Never `overwrite(filter)` / `delete(filter)` in
the hot path — PyIceberg reads the existing files first, which turns a write
into a full rewrite. Maintenance runs out-of-band via **Athena**.

### Compaction

Small-batch commits (the 5-minute `live_lake_writer`) produce ~30 MB files,
which degrade reads over months. Compaction is non-blocking — readers see old
files until the commit lands, then switch atomically.

```sql
CALL lake.system.rewrite_data_files(
    table => 'lake.equities.schwab_universe',
    options => map('target-file-size-bytes', '134217728',
                   'min-file-size-bytes',    '67108864')
);
```

`polygon_raw` is mostly static and needs this rarely; `schwab_universe` and
`schwab_futures` are the ones that accumulate small files.
`OPTIMIZE` requires Iceberg engine version 3.

### Snapshot retention

Iceberg keeps every commit as a snapshot, which grows metadata unboundedly.

```sql
ALTER TABLE lake.equities.polygon_raw
SET TBLPROPERTIES (
    'history.expire.min-snapshots-to-keep' = '20',
    'history.expire.max-snapshot-age-ms'   = '7776000000'  -- 90 days
);

CALL lake.system.expire_snapshots('lake.equities.polygon_raw');
```

Keeps the last 90 days or 20 snapshots, whichever is more. Pinned training
snapshots stay protected.

### Athena dialect trap

**DDL uses Hive backticks; DML uses Trino double quotes.** One query cannot
mix both. See
[`../standards/data/athena_dialects.md`](../standards/data/athena_dialects.md).

---

## 7. Cross-provider unions

Because every bar table shares the canonical column set, a continuous
cross-provider history is a one-line union — this is the join story for ML
training:

```sql
SELECT symbol, timestamp, close, volume, source
FROM lake.equities.polygon_raw
WHERE symbol = 'AAPL' AND timestamp < TIMESTAMP '2025-01-01'
UNION ALL
SELECT symbol, timestamp, close, volume, source
FROM lake.equities.schwab_universe
WHERE symbol = 'AAPL' AND timestamp >= TIMESTAMP '2025-01-01'
ORDER BY timestamp
```

In Python, use `read_arrow()` with the union source rather than hand-rolling
this — it applies the shared dedup with polygon-wins precedence. See
[`data/lake_read_layer.md`](../data/lake_read_layer.md).

---

## 8. Schema evolution

Iceberg format-version 2 supports adding columns (backwards-compatible),
renaming at the catalog level, and type widening (INT → BIGINT). Changes are
recorded as new table metadata, so a pipeline pinning a snapshot reads the
schema as of that snapshot — no surprise changes.

```sql
ALTER TABLE lake.equities.polygon_raw ADD COLUMN open_to_close_ret DOUBLE;
```

Old rows read NULL for the new column; new writes fill it.

---

## 9. Emergency read without the catalog

The Glue metadata layer is required for snapshot pinning, atomic writes,
schema evolution and hidden partition pruning. If the catalog is unavailable,
the Parquet files are still directly readable — less performant, but viable:

```python
duckdb.sql("""
    SELECT * FROM read_parquet(
        's3://{bucket}/iceberg/equities/polygon_raw/data/**/*.parquet',
        hive_partitioning = 1
    )
    WHERE symbol = 'AAPL'
""")
```
