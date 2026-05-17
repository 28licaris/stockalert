# StockAlert Data Platform Plan

Single source of truth for storage and ingestion. Supersedes the previous
`storage_plan.md` and `iceberg_migration_plan.md`.

---

## 1. Goals

- **Source of truth on S3.** ClickHouse becomes a rebuildable serving cache,
  not the canonical store. Drop a CH table, replay from the lake.
- **Cheap.** Storage is rounding-error at this scale; spend the budget on
  query speed and data quality, not byte-shaving.
- **Fast for the two queries that matter:** (a) whole universe on date X
  (cross-sectional); (b) one symbol over N years (time series).
- **Bulletproof.** ACID writes, idempotent ingestion, schema evolution,
  reproducible ML training datasets.
- **Multi-provider.** Polygon, Schwab, Alpaca today. Pluggable for more
  (Databento, IEX) without schema rewrites.
- **ML-ready end state.** A `gold/` layer of features + a snapshot-pinned
  silver layer that lets us reproduce any training run.

## 2. Tech choices (locked)

| Concern | Choice | Why |
|---|---|---|
| Object store | S3 bucket `stock-lake`, `us-east-1` | Existing. Cheapest region. |
| Table format | **Apache Iceberg** | ACID, schema evolution, time travel, `MERGE INTO`. |
| Catalog | **AWS Glue Data Catalog** | Zero ops; native Athena integration; free at our scale. |
| File format | Parquet + Snappy | Iceberg default; good compression + fast decode. |
| Query engines | PyIceberg + DuckDB locally; Athena for ad-hoc SQL | No new infra. |
| Hot tier | ClickHouse (existing) | Live divergence detection, UI charts. Cache only. |
| Data scope | OHLCV bars only (1m + daily) | No tick/quote for now. Bars-only fits all current and near-term use cases. |
| Corp-actions source | Polygon | Has both raw bars and a corp-actions feed. |

## 3. Bucket configuration

- Bucket: `stock-lake` (us-east-1)
- Block all public access: **ON**
- Versioning: **ON**
- Default encryption: SSE-S3
- One bucket, separated by prefix. Multiple buckets would only matter for
  cross-region, separate KMS keys, or access boundaries — none apply.

### Lifecycle rules

| Layer | Transition | Reason |
|---|---|---|
| `iceberg/bronze/` | Standard → Standard-IA at 180d → Glacier Instant Retrieval at 365d | Rarely read once silver exists; the precious raw data so kept in Standard longer than originally planned. |
| `iceberg/silver/` | Standard (no tiering) | Hot ML read path; retrieval fees from constant scans would dwarf any IA savings. |
| `iceberg/gold/` | Standard | Small, hot, rebuildable. |
| All | Abort incomplete multipart uploads at 7d; expire noncurrent versions at 30d | Cost hygiene. |

Glacier **Instant Retrieval**, not Deep Archive — Deep is too slow for
ad-hoc backfill or audit queries. Instant Retrieval has the same
millisecond latency as Standard; only storage cost and retrieval fees
differ.

**Compaction discipline:** Iceberg `rewrite_data_files` targets recent
partitions only (last 90 days). Older files are already well-compacted
from initial ingest, and rewriting tiered data would trigger early-
deletion fees against the 30d (IA) and 90d (Glacier IR) minimum-storage
durations.

### IAM (scoped to bucket)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::stock-lake/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::stock-lake"
    },
    {
      "Effect": "Allow",
      "Action": [
        "glue:GetDatabase", "glue:GetTable", "glue:GetTables",
        "glue:CreateTable", "glue:UpdateTable",
        "glue:GetPartitions", "glue:CreatePartition", "glue:UpdatePartition"
      ],
      "Resource": "*"
    }
  ]
}
```

`s3:DeleteObject` is required for Iceberg compaction + snapshot expiry.

## 4. Layout

```
s3://stock-lake/
└── iceberg/
    ├── bronze/          immutable, append-only, per-provider raw
    │   ├── polygon_minute/
    │   ├── polygon_day/
    │   ├── schwab_minute/
    │   ├── schwab_day/
    │   └── alpaca_minute/
    │
    ├── silver/          cross-provider, one bar per (symbol, ts), source of truth
    │   ├── ohlcv_1m/
    │   ├── ohlcv_daily/
    │   ├── corp_actions/
    │   └── bar_quality/
    │
    └── gold/            ML-ready, rebuildable
        ├── features_1m/
        ├── features_daily/
        └── universes/
```

Iceberg manages partition paths inside each table directory. We never
hand-write Parquet under these prefixes.

## 5. Bronze layer (per-provider, immutable)

One table per provider per kind. Schemas drift between providers (Polygon
has `trade_count` and `vwap`, Schwab doesn't, Databento has microstructure);
separate tables let each evolve independently. Silver normalizes.

### Partition + sort

- **Partition:** `month(ts)` — ~12 partitions/year/table.
- **Sort within file:** `symbol ASC, ts ASC` — row-group min/max stats prune
  per-symbol queries without partitioning by symbol (which would kill
  cross-sectional queries).
- **Target file size:** 128 MB post-compaction.

### Schema (example: `bronze.polygon_minute`)

| Column | Type | Notes |
|---|---|---|
| symbol | string | sort key |
| ts | timestamptz | sort key, UTC |
| open / high / low / close | double | as delivered by provider |
| volume | long | |
| vwap | double | nullable; provider-specific |
| trade_count | long | nullable; provider-specific |
| ingestion_ts | timestamptz | when row landed |
| ingestion_run_id | string | FK to `ingestion_runs` audit table |
| raw_payload_hash | string | hash of source row, detects re-deliveries |

Schwab/Alpaca tables drop `vwap`/`trade_count` if the provider doesn't
supply them — Iceberg schema evolution handles future additions cleanly.

### Write contract

- All writes use Iceberg `MERGE INTO` keyed on `(symbol, ts)`. Idempotent
  under retries. `WHEN NOT MATCHED THEN INSERT`; never `UPDATE` (bronze is
  immutable).
- If Polygon re-delivers a corrected bar (`raw_payload_hash` differs from
  prior write), log a `bar_revision` event into `ingestion_runs` but do not
  overwrite bronze. Silver build picks the latest hash per `(symbol, ts)`.

## 6. Silver layer (canonical, ML-facing)

One bar per `(symbol, ts)`, source of truth for backtests and training.

### `silver.ohlcv_1m`

| Column | Type | Notes |
|---|---|---|
| symbol | string | sort key |
| ts | timestamptz | sort key, UTC |
| open_raw / high_raw / low_raw / close_raw | double | unadjusted, as a trader saw it live |
| open_adj / high_adj / low_adj / close_adj | double | split + dividend adjusted |
| volume_raw | long | unadjusted shares |
| volume_adj | long | split-adjusted shares |
| vwap | double | nullable |
| trade_count | long | nullable |
| source_provider | string | which provider's bar won precedence |
| sources_seen | array&lt;string&gt; | every provider that had this bar |
| ingestion_ts | timestamptz | when this silver row was built |

Dual columns let backtests reconstruct what a trader actually saw (raw)
while ML trains on adjusted prices. Without this, split events look like
75% crashes in training data.

### Provider precedence (config-driven)

Default: `polygon > schwab > alpaca` for minute bars. The first provider
with a bar wins; sources_seen records the rest for QA.

### Adjustment logic

Adjusted columns are computed from `silver.corp_actions`:
- For every split factor `f` on date D, multiply prices before D by `1/f`
  and volume by `f`.
- For every cash dividend `d` on ex-date D, subtract `d` from prices
  before D.

Polygon is the canonical corp-actions source. Adjustments are recomputed
in the silver build job; they are never persisted at ingest time.

### `silver.corp_actions`

| Column | Type | Notes |
|---|---|---|
| symbol | string | |
| ex_date | date | |
| action_type | string | `split`, `cash_dividend`, `stock_dividend`, `spinoff` |
| factor | double | split ratio (e.g., 4.0 for 4-for-1) |
| cash_amount | double | dividend per share |
| announced_at | timestamptz | |
| source_provider | string | always `polygon` for now |

### `silver.bar_quality`

Populated by the silver build job. Partition `month(date)`.

| Column | Type |
|---|---|
| symbol | string |
| date | date |
| expected_bars | int |
| actual_bars | int |
| gap_count | int |
| max_gap_minutes | int |
| providers_seen | array&lt;string&gt; |
| disagreements | int |
| backfill_attempts | int |

This is the data-quality ledger that catches silent provider drops. A
nightly job alerts on:
- `actual_bars / expected_bars < 0.95` for any tracked symbol
- new provider disagreements on the same minute
- corp actions without corresponding silver updates

## 7. Gold layer (ML features, rebuildable)

Anything in gold is reconstructible from silver. Never hand-edited.

### `gold.features_1m`

| Column | Type | Notes |
|---|---|---|
| symbol | string | |
| ts | timestamptz | |
| return_1m / 5m / 15m / 1h / 1d | double | log returns |
| realized_vol_1h / 1d / 5d | double | rolling std of log returns |
| rsi_14 / macd / tsi / ema_50 / ema_200 | double | from `app/indicators/` |
| volume_z_20 | double | volume z-score over 20-bar window |
| feature_set_version | string | bumps when feature definitions change |

`feature_set_version` is critical for ML: feature drift is a silent
training-vs-serving bug. Old training runs reference an old version;
new runs use the new version; both coexist in the same table.

### `gold.universes`

Point-in-time investable universe. Daily snapshot of "what symbols would
we have traded today." Prevents survivorship bias in backtests.

| Column | Type |
|---|---|
| date | date |
| symbol | string |
| is_active | bool |
| market_cap_usd | double |
| avg_daily_volume_usd | double |
| inclusion_reason | string |

## 8. Ingestion paths

All three paths produce the same `CanonicalBar` and write to **bronze only**.
Silver and gold are built by separate jobs, never by ingest.

```python
class CanonicalBar:
    symbol: str
    ts: datetime  # UTC
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None
    trade_count: int | None
    provider: str
    ingestion_ts: datetime
    raw_payload_hash: str
```

### Path A — live streaming (T+0 → T+5min lake)

1. Provider WebSocket → existing async batcher → ClickHouse `ohlcv_1m`.
   ([app/services/live/monitor_service.py](../app/services/live/monitor_service.py),
   [app/db/batcher.py](../app/db/batcher.py))
2. **New** `live_lake_writer` job runs every 5 minutes:
   - Reads CH `ohlcv_1m` for the last 15 minutes
   - Groups by provider
   - `MERGE INTO bronze.{provider}_minute` per group
   - Records run in `ingestion_runs`

Replaces the current "lake gets data only at T+1 day via Polygon flat
files" gap. Schwab and Alpaca streamed bars now land in the lake too.

### Path B — nightly flat-file archive (T+1)

Existing [nightly_lake_refresh.py](../app/services/nightly_lake_refresh.py)
job, retargeted:

1. At 07:00 UTC pull Polygon flat files for yesterday.
2. Canonicalize via existing
   [PolygonFlatFilesClient](../app/providers/polygon_flatfiles.py).
3. `MERGE INTO bronze.polygon_minute` and `bronze.polygon_day`.
4. Trigger silver build for that date.

Polygon flat files are higher quality than the live WS feed (consolidated
SIP), so they overwrite live bars for the same `(symbol, ts)` — handled
naturally by `MERGE INTO` ordering on `ingestion_ts`.

### Path C — gap-fill backfill (T+anything)

Existing [backfill_service.py](../app/services/backfill_service.py)
two-path job queue, retargeted:

- Quick (30d latency-first) and deep (365d completeness-first) paths
  remain. Output goes through the canonical sink contract into
  `bronze.{provider}_{kind}`.
- Per-symbol job dedup remains.
- Sink fan-out pattern from
  [flatfiles_sinks.py](../app/services/flatfiles_sinks.py) is generalized:
  `BronzeIcebergSink` replaces `LakeSink`.

### Silver build (daily, separate job)

```
silver_build.py --date YYYY-MM-DD
```

1. Read all `bronze.*_minute` and `bronze.*_day` partitions for the date.
2. Apply provider precedence config → one bar per `(symbol, ts)`.
3. Pull `silver.corp_actions` to compute adjusted columns.
4. `MERGE INTO silver.ohlcv_1m` and `silver.ohlcv_daily`.
5. Compute and `MERGE INTO silver.bar_quality`.
6. Record in `ingestion_runs`.

Idempotent: rerunning for the same date is a no-op unless bronze changed.

### Gold build (daily, separate job)

```
gold_build.py --date YYYY-MM-DD --feature-set v3
```

Reads silver, computes features, writes `gold.features_*`. Versioned by
`feature_set_version`.

## 9. Idempotency & audit

### Iceberg `MERGE INTO` = correctness layer

Every write to bronze/silver/gold goes through `MERGE INTO`. Atomic at
the row level; reruns are no-ops. There is no path that writes Parquet
files without going through Iceberg.

### `ingestion_runs` (in ClickHouse) = operational layer

Replaces the current `lake_archive_watermarks`. Audit/observability only —
cannot corrupt data, since correctness is enforced by Iceberg.

| Column | Type |
|---|---|
| run_id | UUID |
| source | string (`polygon_flatfiles`, `live_writer`, `backfill_quick`, etc.) |
| target_table | string |
| period_start / period_end | timestamptz |
| status | `running`, `succeeded`, `failed`, `noop` |
| row_count | long |
| snapshot_id_before / snapshot_id_after | long (Iceberg snapshot IDs) |
| error | string nullable |
| started_at / finished_at | timestamptz |
| code_git_sha | string |

`snapshot_id_before/after` closes the loop: every operational run is
linked to specific Iceberg snapshots.

## 10. ML reproducibility

### Snapshot pinning is mandatory for saved models

Every training run records the Iceberg snapshot IDs of the silver and
gold tables it read. Saved model artifacts must carry these IDs.

### `model_training_runs` (in ClickHouse)

| Column | Type |
|---|---|
| run_id | UUID |
| started_at | timestamptz |
| silver_snapshot_id | long |
| gold_snapshot_id | long |
| feature_set_version | string |
| code_git_sha | string |
| params | string (JSON) |
| metrics | string (JSON) |
| artifact_uri | string |

### Snapshot retention policy

- Untagged snapshots expire after **30 days** (Iceberg
  `expire_snapshots` weekly).
- Snapshots tagged via `iceberg_table.manage_snapshots().create_tag(...)`
  **never expire** until the tag is dropped.
- Every saved model gets a tag: `model_{run_id}_silver`,
  `model_{run_id}_gold`.

Cost impact: pennies/month at our scale. Reproducibility impact:
unbounded.

## 11. Maintenance

| Job | Cadence | What it does |
|---|---|---|
| Compaction (`rewrite_data_files`) | Weekly per bronze table; monthly silver | Merge small files toward 128MB target. |
| Snapshot expiry (`expire_snapshots`) | Weekly | Drop untagged snapshots > 30d. Reclaim S3. |
| Orphan file cleanup (`remove_orphan_files`) | Monthly | Catches partial-write residue. |
| Manifest rewrite | When manifest count > 100 per partition | Keeps planning fast. |
| `bar_quality` alert sweep | Daily | Pages on coverage regressions. |

Concurrent-write rule: never run two writers on the same partition
simultaneously. Live writer holds a per-provider lock; nightly archive
runs after live writer's last flush of the day.

## 12. Cost (rough, monthly)

At target scale (10k tickers × 1m + daily bars × eventually 20yr history):

| Item | Estimate |
|---|---|
| S3 storage (Parquet+Snappy, ~20 GB) | < $1 |
| S3 PUTs/GETs from ingest | < $2 |
| Glue catalog | $0 (well under free tier) |
| Athena scans | Cents per backtest (partition + sort prune to MB-range) |
| ClickHouse (your existing instance) | unchanged |

Total lake cost: **a few dollars/month at maturity**. Storage is not
where money is spent. Egress is — keep DuckDB/Athena queries
projection-pushed and run them in the same region as the bucket.

## 13. Phased migration

### Phase 0 — infra (1–2 days)
- Provision `stock-lake` bucket with versioning, lifecycle, IAM.
- Provision Glue database `stock_lake`.
- Add PyIceberg config; verify connectivity from a script.
- Existing nightly archive keeps running. No reader changes yet.

### Phase 1 — bronze on Iceberg (3–5 days)
- Create `bronze.polygon_minute`, `bronze.polygon_day`,
  `bronze.schwab_minute`, `bronze.schwab_day`, `bronze.alpaca_minute` as
  Iceberg tables. Partition `month(ts)`, sort `(symbol, ts)`.
- Use `add_files` to register existing
  `raw/provider=*/kind=*/year=*/date=*.parquet` into bronze without
  rewrites. Verify row counts against the existing watermark ledger.
- Build `BronzeIcebergSink` to replace `LakeSink`. Existing sink fan-out
  ([flatfiles_sinks.py](../app/services/flatfiles_sinks.py)) keeps working.
- Run one round of compaction to merge daily files into monthly.

### Phase 2 — live → bronze (2–3 days)
- New `live_lake_writer` job: every 5 min, CH `ohlcv_1m` →
  `bronze.{provider}_minute` via `MERGE INTO`.
- Lake now ingests live data, not just T+1 flat files.

### Phase 3 — silver + corp actions (3–5 days)
- Build `silver.corp_actions` ingestion from Polygon corp-actions API.
- Build `silver_build.py` daily job (provider precedence, adjustments,
  `bar_quality`).
- Backfill silver for full history (monthly batches).
- Wire `bar_quality` alerts.

### Phase 4 — flip readers (2 days)
- Backtest + training paths read from silver via PyIceberg + DuckDB.
- ClickHouse becomes a serving cache: live divergence, UI charts (recent
  N days). Add a CLI: rebuild CH from silver.
- Retire `lake_archive_watermarks`; replace with `ingestion_runs`.

### Phase 5 — gold + reproducibility (ongoing)
- `gold.features_1m`, `gold.features_daily`, `gold.universes`.
- `model_training_runs` registry.
- Snapshot tagging on every saved model.

### Phase 6 — retire legacy lake prefix (after 30d of green Phase 4)
- Delete `s3://stock-lake/raw/` after confirming nothing reads it.
- Delete `LakeArchiveWriter` and the old `LakeSink`.

## 14. Operational considerations

- **Per-partition single-writer rule.** Iceberg handles atomic commits,
  but two writers targeting the same partition risk wasted work. Live
  writer holds a per-provider in-memory lock; nightly archive checks
  that the live writer has flushed.
- **Schema evolution.** Add columns via Iceberg `add_column`. Never
  rename — readers may pin to old snapshots.
- **Time zone discipline.** Every `ts` column is UTC. Display-layer
  conversion only.
- **Disaster recovery.** S3 versioning + Iceberg snapshot history
  together cover ~all recovery scenarios. For an extra belt: monthly
  copy of `iceberg/silver/` metadata to a second region.

## 15. Open items deferred (explicitly out of scope for now)

- Tick / quote / order-book data. Bars-only fits all current ML use
  cases. Revisit when a strategy needs microstructure.
- Options, futures, crypto. Schema is bars-and-equities for now.
- Real-time feature serving (sub-second). Today's "every 5 min" lake
  cadence is fine; if we need sub-second, that's a separate online
  feature store (Redis/Feast), not the lake.
- Cross-region replication of the bucket.
- Self-hosted REST Iceberg catalog (e.g., Nessie) — Glue is sufficient
  until we need branching or git-like workflows on data.
