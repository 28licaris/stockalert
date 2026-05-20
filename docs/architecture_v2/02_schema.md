# 02 — Schema

All four lake datasets share the **same canonical OHLCV schema** so
joins and unions are trivial.

## Canonical OHLCV columns

| Column | Type | Required | Notes |
|---|---|---|---|
| `symbol` | STRING | yes | Uppercase, normalized (`watchlist_repo.normalize_member_symbol`) |
| `timestamp` | TIMESTAMP (μs UTC) | yes | UTC always. Naive timestamps must be normalized at write time |
| `open` | DOUBLE | yes | First trade of the minute |
| `high` | DOUBLE | yes | Max of the minute |
| `low` | DOUBLE | yes | Min of the minute |
| `close` | DOUBLE | yes | Last trade of the minute |
| `volume` | DOUBLE | yes | Shares traded in the minute. DOUBLE (not BIGINT) for split-factor multiplications |
| `vwap` | DOUBLE | no (nullable) | Volume-weighted average price; 0 / null if provider doesn't supply |
| `trade_count` | INT | no (nullable) | Number of trades; 0 / null if not supplied |
| `source` | STRING | yes | Origin tag (see below) |
| `adj_factor` | DOUBLE | yes (adjusted tables only) | Cumulative split factor applied to this row. `1.0` = no splits adjusted; `0.25` = stock has had a 4-for-1 split since |

### `source` tag conventions

| Value | Where it lives | Meaning |
|---|---|---|
| `"polygon-raw"` | `equities.polygon_raw` | Polygon flat-files, untouched |
| `"polygon-adjusted"` | `equities.polygon_adjusted` | Polygon raw + corp-actions applied by `polygon_adjustment_job` |
| `"schwab-live"` | `equities.schwab_universe`, CH.ohlcv_1m | Schwab CHART_EQUITY WebSocket |
| `"schwab-rest-pricehistory"` | `equities.schwab_universe`, CH.ohlcv_1m | Schwab REST tip-fill (on-add) |
| `"schwab-nightly"` | (legacy v1; not used in v2 daily path) | Schwab REST nightly batch |

### Why `adj_factor` as a column

For ML training where features depend on price level (e.g. "trade
when close > 20×book_value"), you need to know what scale the
price is on. `adj_factor` tells the training pipeline how to back-
compute the raw price if needed:

```python
raw_close = adjusted_close * adj_factor
```

Otherwise, two symbols at the same nominal price could mean very
different things — one might have had a 10:1 split that adjusted
its $200 price down to $20.

`adj_factor = 1.0` on the schwab_universe table because Schwab
adjusts at the API and doesn't expose the pre-split price.
`adj_factor` on `polygon_adjusted` carries real information because
we computed it from raw + splits.

## Iceberg DDL — `equities.polygon_raw`

```sql
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
    source      STRING NOT NULL DEFAULT 'polygon-raw'
)
PARTITIONED BY (
    bucket(32, symbol),
    month(timestamp)
)
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'zstd',
    'write.distribution-mode' = 'hash',
    'write.target-file-size-bytes' = '134217728',     -- 128 MB target
    'write.parquet.row-group-size-bytes' = '16777216' -- 16 MB row groups
);
```

**Note**: no `adj_factor` on raw tables — they're unadjusted by
definition.

## Iceberg DDL — `equities.polygon_adjusted`

```sql
CREATE TABLE lake.equities.polygon_adjusted (
    symbol      STRING NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    open        DOUBLE NOT NULL,
    high        DOUBLE NOT NULL,
    low         DOUBLE NOT NULL,
    close       DOUBLE NOT NULL,
    volume      DOUBLE NOT NULL,
    vwap        DOUBLE,
    trade_count INT,
    source      STRING NOT NULL DEFAULT 'polygon-adjusted',
    adj_factor  DOUBLE NOT NULL DEFAULT 1.0
)
PARTITIONED BY (
    bucket(32, symbol),
    month(timestamp)
)
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'zstd',
    'write.distribution-mode' = 'hash',
    'write.target-file-size-bytes' = '134217728',
    'write.parquet.row-group-size-bytes' = '16777216',
    -- Merge-on-read for incremental corp-action rebuilds (UPDATE/DELETE/MERGE)
    'write.merge.mode' = 'merge-on-read',
    'write.update.mode' = 'merge-on-read',
    'write.delete.mode' = 'merge-on-read'
);
```

## Iceberg DDL — `equities.schwab_universe`

```sql
CREATE TABLE lake.equities.schwab_universe (
    symbol      STRING NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    open        DOUBLE NOT NULL,
    high        DOUBLE NOT NULL,
    low         DOUBLE NOT NULL,
    close       DOUBLE NOT NULL,
    volume      DOUBLE NOT NULL,
    vwap        DOUBLE,
    trade_count INT,
    source      STRING NOT NULL,             -- "schwab-live" or "schwab-rest-pricehistory"
    adj_factor  DOUBLE NOT NULL DEFAULT 1.0  -- Always 1.0; Schwab adjusts at API
)
PARTITIONED BY (
    bucket(16, symbol),   -- 16 buckets for Top-250-by-ADV universe (Gate 13): ~250 syms / 16 ≈ 16 per bucket
    month(timestamp)
)
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'zstd',
    'write.merge.mode' = 'merge-on-read',
    'write.update.mode' = 'merge-on-read',
    'write.delete.mode' = 'merge-on-read'
);
```

## Iceberg DDL — `equities.market_corp_actions`

```sql
CREATE TABLE lake.equities.market_corp_actions (
    symbol       STRING NOT NULL,
    ex_date      DATE NOT NULL,            -- Ex-dividend / split date
    action_type  STRING NOT NULL,          -- 'split' | 'dividend' | 'special_dividend'
    split_ratio  DOUBLE,                   -- e.g. 4.0 for a 4-for-1 split; null for dividends
    cash_amount  DOUBLE,                   -- USD per share; null for splits
    declared_at  DATE,                     -- when the corp action was announced
    raw_payload  STRING                    -- Provider-native JSON for audit
)
PARTITIONED BY (month(ex_date))
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'zstd'
);
```

## Cumulative split factor — the math

For a symbol with splits at `ex_date_1 < ex_date_2 < ...`, the
cumulative factor applied to a bar at time `t` is:

```
F(symbol, t) = ∏ split_ratio_i  for all splits where ex_date_i > t
```

i.e. multiply together the ratios of all FUTURE splits.

Then:
```
adjusted_open  = raw_open  / F
adjusted_high  = raw_high  / F
adjusted_low   = raw_low   / F
adjusted_close = raw_close / F
adjusted_volume = raw_volume * F
adj_factor     = F
```

Volume is multiplied (more shares post-split); prices are divided.

### Example

AAPL had a 4-for-1 split on `2020-08-31`.

| Bar timestamp | Raw close | F (cumulative future-splits factor) | Adjusted close |
|---|---|---|---|
| 2020-08-30 16:00 | $500 | 4.0 (the future split) | $125 |
| 2020-08-31 09:30 | $129 (post-split) | 1.0 (no future splits) | $129 |
| 2024-06-15 10:00 | $216 | 1.0 | $216 |

A chart that shows 2020 + 2024 in one view shows continuous prices
($125 → ... → $216) — no $500-to-$129 cliff at the split.

## CH (live tier) schema

The live ClickHouse schema is unchanged from v1, since the FastAPI
chart endpoint reads it. v2 only changes WHO writes to it.

```sql
CREATE TABLE ohlcv_1m (
    symbol        LowCardinality(String),
    timestamp     DateTime64(3, 'UTC'),
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64,
    vwap          Float64 DEFAULT 0,
    trade_count   UInt32 DEFAULT 0,
    source        LowCardinality(String) DEFAULT '',
    version       UInt64 DEFAULT 0   -- ReplacingMergeTree(version) — later writes win
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, timestamp);
```

`source` and `version` columns let v2's direct Schwab REST writes
coexist with v1's silver-derived writes during the migration. After
Phase 5 cutover, only Schwab writes (live + REST) remain.

## Cross-table queries — the schema parity payoff

Because `equities.polygon_adjusted` and `equities.schwab_universe` share the
same column set (with `adj_factor` defaulting to 1.0 on Schwab), a
continuous cross-provider view is a one-line UNION:

```sql
SELECT symbol, timestamp, close, volume, adj_factor, source
FROM lake.equities.polygon_adjusted
WHERE symbol = 'AAPL' AND timestamp < TIMESTAMP '2025-01-01'

UNION ALL

SELECT symbol, timestamp, close, volume, adj_factor, source
FROM lake.equities.schwab_universe
WHERE symbol = 'AAPL' AND timestamp >= TIMESTAMP '2025-01-01'

ORDER BY timestamp
```

No schema massaging. `source` tag makes it auditable. `adj_factor`
columns line up (1.0 in Schwab passthrough = "this is a Schwab-
already-adjusted bar"). This is the join story for ML training.

## Schema evolution

Iceberg's format-version 2 supports:
- Adding columns (backwards-compatible)
- Renaming columns (catalog-level)
- Type widening (INT → BIGINT)

Schema changes are first-class operations and are recorded as new
table-version metadata. ML training pipelines that pin a snapshot
read the schema-as-of-that-snapshot — no surprise schema changes.

When adding a column in the future:
```python
spark.sql("ALTER TABLE lake.equities.polygon_adjusted ADD COLUMN open_to_close_ret DOUBLE")
```

Old rows show NULL for the new column; new writes fill it.

## See also

- [03_s3_layout.md](03_s3_layout.md) — where these tables actually live in S3
- [04_spark.md](04_spark.md) — SQL examples using these schemas
- [06_migration.md](06_migration.md) — how to materialize these tables from v1
