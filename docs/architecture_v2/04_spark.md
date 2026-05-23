# 04 — PySpark in the v2 System

## What Spark is for (and isn't)

| Use case | Engine | Why |
|---|---|---|
| **Live API chart query** | ClickHouse | <200ms target; no Spark on the request path |
| **Operator ad-hoc** (one user, one symbol) | DuckDB | Single-process, no cluster; ~1-3s for 5y of one symbol |
| **`polygon_adjustment_job`** (whole-market, weekly) | Local PySpark by default; EMR Serverless if local can't keep up | Tree-join on 5y × 12k symbols fits a 16 GB dev box; EMR Serverless is the escape hatch (Gate 5) |
| **ML feature engineering** (whole-market, batch) | Local PySpark; EMR Serverless escape hatch | Same |
| **Backtest runs** (10s-100s of symbols, hours of compute) | Spark or local Python | Either works |
| **Snapshot-pinned training data export** | Spark (parallel writes) | Iceberg time-travel + parallel Parquet output |
| **Single-symbol deep history** (chart zoom past CH) | DuckDB via `/api/v1/lake/bars` endpoint **+ MCP tools** (`lake_bars`, `lake_cross_provider_diff`, `lake_snapshot_list`) | One-symbol scan is fast enough for DuckDB; agent gets the same query path (Gate 7) |

**Spark is only invoked for batch jobs.** It never sits on the live
request path.

**Gate 5 policy:** local-first. Default runner for every Spark script
in this doc is `python scripts/spark/<job>.py` on a 16 GB dev box.
EMR Serverless setup ships in Phase 1 (CV4) so the on-demand AWS path
exists, but jobs only escalate there if local wall-clock exceeds
~30 min. The EMR launcher details below are the escape-hatch contract,
not the steady state.

## Required JARs

For Spark + Iceberg + Glue + S3, you need:

```
org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.6.0
org.apache.iceberg:iceberg-aws-bundle:1.6.0
```

EMR Serverless includes these by default if you use the
`emr-7.0.0` release label or newer.

Local Spark: pass via `spark-submit --packages` or `spark.jars.packages`.

## The Spark session helper

`scripts/spark/__init__.py` exports `get_spark()` so every Spark
script uses the same config.

```python
"""Shared Spark setup for v2 lake batch jobs.

Works identically locally (`pip install pyspark`), on EMR Serverless,
and on Databricks — only the catalog endpoint config differs by env.
"""
from pyspark.sql import SparkSession
import os


def get_spark(app_name: str = "stockalert-batch") -> SparkSession:
    builder = (
        SparkSession.builder
        .appName(app_name)
        # Iceberg extensions
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        # Glue catalog named "lake" — matches Iceberg writers in app/
        .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.lake.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
        .config("spark.sql.catalog.lake.warehouse", os.environ["STOCK_LAKE_BUCKET_S3"])
        .config("spark.sql.catalog.lake.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        # AWS region
        .config("spark.hadoop.fs.s3a.region", os.environ.get("AWS_REGION", "us-east-1"))
        # Performance
        .config("spark.sql.parquet.enableVectorizedReader", "true")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
    )
    # Local-dev override: include the Iceberg packages via spark.jars.packages
    if os.environ.get("STOCKALERT_SPARK_LOCAL_MODE") == "true":
        builder = builder.config(
            "spark.jars.packages",
            "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.6.0,"
            "org.apache.iceberg:iceberg-aws-bundle:1.6.0",
        )
    return builder.getOrCreate()
```

## Real query patterns

### Pattern 1 — Single-symbol deep history (operator ad-hoc)

```python
from scripts.spark import get_spark

spark = get_spark()

df = spark.sql("""
    SELECT * FROM lake.equities.polygon_adjusted
    WHERE symbol = 'AAPL'
      AND timestamp BETWEEN '2020-01-01' AND '2024-12-31'
    ORDER BY timestamp
""")
print(f"rows: {df.count():,}")     # ~490,000
df.show(5)
```

Wall-clock: ~3-5 seconds (single symbol × 5y = ~9 GB scanned thanks
to bucket(32) partitioning).

### Pattern 2 — Cross-provider continuity (the join you want for ML)

```python
df = spark.sql("""
    SELECT * FROM (
        SELECT symbol, timestamp, open, high, low, close, volume,
               adj_factor, source
        FROM lake.equities.polygon_adjusted
        WHERE symbol = 'AAPL'
          AND timestamp < TIMESTAMP '2025-01-01'

        UNION ALL

        SELECT symbol, timestamp, open, high, low, close, volume,
               1.0 AS adj_factor,    -- Schwab already adjusted
               source
        FROM lake.equities.schwab_universe
        WHERE symbol = 'AAPL'
          AND timestamp >= TIMESTAMP '2025-01-01'
    ) ORDER BY timestamp
""")
df.write.parquet("s3://stockalert-features/aapl_continuous_5y.parquet")
```

Polygon for history, Schwab for present, continuous timeline.

### Pattern 3 — Whole-market feature engineering (production batch)

```python
from pyspark.sql.functions import col, lag, log, stddev, avg, window
from pyspark.sql.window import Window

bars = spark.sql("""
    SELECT symbol, timestamp, close, volume
    FROM lake.equities.polygon_adjusted
    WHERE timestamp >= TIMESTAMP '2023-01-01'
""")

# Per-symbol log returns
w = Window.partitionBy("symbol").orderBy("timestamp")
bars = bars.withColumn("log_return", log(col("close") / lag("close").over(w)))

# Rolling 30-min realized volatility per symbol
features = (
    bars.groupBy("symbol", window("timestamp", "30 minutes"))
    .agg(
        stddev("log_return").alias("realized_vol_30m"),
        avg("volume").alias("avg_vol_30m"),
    )
)

(features.write
    .mode("overwrite")
    .partitionBy("symbol")
    .parquet("s3://stockalert-features/realized_vol_30m_v1/"))
```

12k symbols × 2y × 1m bars ≈ ~6B rows; runs in ~10 min on a 16-worker
EMR Serverless app. **Cost: ~$2-3 per run.**

### Pattern 4 — Snapshot-pinned reproducibility

```python
# Read the exact state of polygon_adjusted as of a specific snapshot.
# The training pipeline pins this snapshot_id in its config, so a model
# trained today can be re-trained on byte-identical data tomorrow.
df = spark.sql("""
    SELECT * FROM lake.equities.polygon_adjusted
    VERSION AS OF 1234567890
    WHERE symbol IN ('AAPL', 'MSFT', 'NVDA')
""")

# Equivalent timestamp form:
df = spark.sql("""
    SELECT * FROM lake.equities.polygon_adjusted
    TIMESTAMP AS OF '2025-01-15 12:00:00'
    WHERE symbol IN ('AAPL', 'MSFT', 'NVDA')
""")
```

Snapshot IDs are surfaced via `lake.equities.polygon_adjusted.snapshots`:

```python
spark.sql("SELECT * FROM lake.equities.polygon_adjusted.snapshots ORDER BY committed_at DESC").show()
```

### Pattern 5 — Incremental read (changed rows only)

```python
# Only rows touched since the prior training run.
# Useful when corp_actions fire and only a few symbols' history changes.
df = spark.sql("""
    SELECT * FROM lake.equities.polygon_adjusted.changes
    WHERE _change_type IN ('INSERT', 'UPDATE_AFTER')
      AND _commit_snapshot_id > 1234567890
""")
```

Saves compute when most of the table is unchanged between runs.

### Pattern 6 — Bar-quality audit across providers

```python
# Where do Polygon and Schwab disagree on the same (symbol, timestamp)?
# Used as a data-quality probe.
diff = spark.sql("""
    SELECT p.symbol, p.timestamp,
           p.close AS polygon_close,
           s.close AS schwab_close,
           abs(p.close - s.close) AS diff
    FROM lake.equities.polygon_adjusted p
    JOIN lake.equities.schwab_universe s
      ON p.symbol = s.symbol AND p.timestamp = s.timestamp
    WHERE abs(p.close - s.close) > 0.01
""")
```

Common findings: corp-action-adjustment timing skew (Polygon's
ex-date may differ by 1 trading day from Schwab's).

## `polygon_adjustment_job` — the canonical Spark job

```python
"""scripts/spark/polygon_adjustment_job.py

Reads equities.polygon_raw + equities.market_corp_actions → writes equities.polygon_adjusted.

Invoked:
  - Once after the initial 5y bulk load.
  - Weekly via EMR Serverless cron to incorporate new corp_actions.
  - On-demand: `scripts/run_polygon_adjustment.py --symbols AAPL,NVDA`.
"""
import argparse
from datetime import date
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from scripts.spark import get_spark, record_run


def adjust(symbols: list[str] | None, since: date | None) -> tuple[int, int]:
    spark = get_spark("polygon_adjustment")

    # 1) Read raw bars + corp_actions
    raw = spark.sql("SELECT * FROM lake.equities.polygon_raw")
    if symbols:
        raw = raw.where(F.col("symbol").isin(symbols))
    if since:
        raw = raw.where(F.col("timestamp") >= F.lit(since))

    splits = spark.sql("""
        SELECT symbol, ex_date, split_ratio
        FROM lake.market_corp_actions
        WHERE action_type = 'split' AND split_ratio != 1.0
    """)

    # 2) Per-bar cumulative future-splits factor.
    #    F(t) = ∏ split_ratio_i over splits with ex_date_i > t.
    #
    #    Implementation: left-join raw × splits on symbol, conditionally
    #    include each split's log(ratio) only when ex_date > bar.timestamp,
    #    aggregate via sum-of-logs then exponentiate. Bars with no future
    #    splits get sum=NULL → exp=NULL → coalesce → 1.0. This handles bars
    #    before, between, and after splits in one expression, and correctly
    #    accumulates multiple future splits (the previous MIN-on-cum_factor
    #    approach picked only the latest split's contribution).
    adjusted = (
        raw.join(splits, on="symbol", how="left")
        .groupBy(raw.symbol, raw.timestamp, raw.open, raw.high, raw.low,
                 raw.close, raw.volume, raw.vwap, raw.trade_count)
        .agg(
            F.coalesce(
                F.exp(F.sum(
                    F.when(F.col("ex_date") > raw.timestamp, F.log("split_ratio"))
                )),
                F.lit(1.0),
            ).alias("adj_factor")
        )
        .select(
            F.col("symbol"),
            F.col("timestamp"),
            (F.col("open") / F.col("adj_factor")).alias("open"),
            (F.col("high") / F.col("adj_factor")).alias("high"),
            (F.col("low") / F.col("adj_factor")).alias("low"),
            (F.col("close") / F.col("adj_factor")).alias("close"),
            (F.col("volume") * F.col("adj_factor")).alias("volume"),
            F.col("vwap"),
            F.col("trade_count"),
            F.lit("polygon-adjusted").alias("source"),
            F.col("adj_factor"),
        )
    )

    # 3) Idempotent upsert via Iceberg merge-on-read
    adjusted.writeTo("lake.equities.polygon_adjusted").using("iceberg").overwritePartitions()

    return adjusted.select("symbol").distinct().count(), adjusted.count()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="csv list; omit for whole market")
    p.add_argument("--since", help="YYYY-MM-DD; omit for full history")
    args = p.parse_args()

    syms = args.symbols.split(",") if args.symbols else None
    since_date = date.fromisoformat(args.since) if args.since else None

    n_symbols, n_rows = adjust(syms, since_date)
    record_run(
        job_name="polygon_adjustment_job",
        rows_written=n_rows,
        symbols_processed=n_symbols,
        status="ok",
    )


if __name__ == "__main__":
    main()
```

### Run modes

| Where | Command | Cost |
|---|---|---|
| **Local (dev)** | `STOCKALERT_SPARK_LOCAL_MODE=true python scripts/spark/polygon_adjustment_job.py --symbols AAPL --since 2020-01-01` | $0 |
| **CodeBuild (one-shot ops)** | `aws codebuild start-build --project-name polygon-adjust` | ~$0.50/run |
| **EMR Serverless (prod schedule)** | `aws emr-serverless start-job-run ...` | ~$2-3 per whole-market rebuild |

## EMR Serverless setup

### One-time

```bash
# Create EMR Serverless application
aws emr-serverless create-application \
    --release-label emr-7.0.0 \
    --type SPARK \
    --name stockalert-spark \
    --initial-capacity '{
        "DRIVER":   {"workerCount":1,"workerConfiguration":{"cpu":"4 vCPU","memory":"16 GB"}},
        "EXECUTOR": {"workerCount":4,"workerConfiguration":{"cpu":"4 vCPU","memory":"16 GB"}}
    }' \
    --maximum-capacity '{"cpu":"64 vCPU","memory":"256 GB"}'
```

This is **pay-per-job** — workers spin up on submit, tear down when
done. Idle = $0.

### Submitting a job

```bash
aws emr-serverless start-job-run \
    --application-id <app-id> \
    --execution-role-arn arn:aws:iam::ACCT:role/stockalert-spark-emr \
    --name "polygon_adjustment_2025-W20" \
    --job-driver '{
        "sparkSubmit": {
            "entryPoint": "s3://stockalert-code/spark/polygon_adjustment_job.py",
            "entryPointArguments": ["--since", "2025-05-13"],
            "sparkSubmitParameters":
                "--conf spark.executor.cores=4 \
                 --conf spark.dynamicAllocation.enabled=true \
                 --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.6.0,org.apache.iceberg:iceberg-aws-bundle:1.6.0"
        }
    }'
```

### Scheduling (EventBridge)

```bash
# Weekly cron — Sunday 06:00 UTC, before the Monday charting load
aws events put-rule \
    --name polygon-adjustment-weekly \
    --schedule-expression "cron(0 6 ? * SUN *)"

# Target: invoke a Lambda that calls emr-serverless start-job-run
aws events put-targets \
    --rule polygon-adjustment-weekly \
    --targets '[{"Id":"1","Arn":"arn:aws:lambda:...:function:trigger-polygon-adjustment"}]'
```

## Local Spark development workflow

```bash
# In a dev shell:
pip install pyspark==3.5.* 'pyiceberg[s3fs,glue]'

# AWS credentials + lake bucket (use your actual profile + bucket).
export AWS_PROFILE=<your-aws-profile>
export STOCK_LAKE_BUCKET_S3=s3://<your-bucket>/equities/
export STOCKALERT_SPARK_LOCAL_MODE=true

# Run any Spark job:
python scripts/spark/polygon_adjustment_job.py --symbols AAPL --since 2024-01-01
```

Local Spark on a laptop handles 10-100 GB scans comfortably (16 GB
RAM). Beyond that → EMR Serverless.

## Cost monitoring

For each EMR Serverless job, AWS tags the cost with the job name.
Track via CloudWatch:

```bash
aws cloudwatch get-metric-statistics \
    --namespace AWS/EMRServerless \
    --metric-name TotalAppCost \
    --dimensions Name=ApplicationId,Value=<app-id> \
    --start-time 2025-05-01T00:00:00Z \
    --end-time 2025-05-31T00:00:00Z \
    --period 86400 \
    --statistics Sum
```

Budget alert: set CloudWatch alarm on EMR Serverless cost >$20/month.
Triggers an SNS notification.

## Performance tuning

| Issue | Symptom | Fix |
|---|---|---|
| Small files | >10,000 files in a partition | Run compaction (see [07_runbook.md](07_runbook.md)) |
| Skewed bucket | One bucket >> others by file count | Increase bucket count via partition evolution |
| Slow whole-table scan | Job takes >1h for what should be 15min | Check predicate pushdown — verify partition filters are in WHERE clause, not in a UDF |
| OOM on driver | Driver crashes with java heap | Reduce broadcast joins; check for accidental `.collect()` |
| Iceberg commit conflicts | Two jobs writing to same partition | Use Iceberg's transaction retry (configured by default in iceberg-spark-runtime) |

## See also

- [02_schema.md](02_schema.md) — column definitions used in these queries
- [03_s3_layout.md](03_s3_layout.md) — partition strategy that makes these queries fast
- [07_runbook.md](07_runbook.md) — operator procedures (compaction, snapshot cleanup)
- [06_migration.md](06_migration.md) — when each Spark job lands in the migration phases
