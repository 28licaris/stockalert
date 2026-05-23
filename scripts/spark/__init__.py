"""
Shared Spark setup + bookkeeping for v2 lake batch jobs.

Every script in `scripts/spark/` calls `get_spark()` so the catalog
config, Iceberg extensions, and AWS region are identical across local
dev, CodeBuild, and EMR Serverless.

PySpark is an OPTIONAL dependency (`poetry install --with spark`) —
this module imports it lazily so the rest of the codebase doesn't pay
a 250 MB install cost for the rare operator who runs Spark jobs.

Spec: docs/architecture_v2/04_spark.md.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyspark.sql import SparkSession  # pragma: no cover

logger = logging.getLogger(__name__)

# Catalog name used by every v2 Spark job. Matches the Iceberg catalog
# alias in app/services/iceberg_catalog.py so Spark queries
# (`SELECT * FROM lake.equities.polygon_raw`) reference the same
# physical Glue database as PyIceberg writes from app/services/.
_CATALOG_NAME = "lake"


def get_spark(app_name: str = "stockalert-batch") -> "SparkSession":
    """Build (or return existing) SparkSession wired to the AWS Glue
    catalog at `lake`.

    Configuration matches `docs/architecture_v2/04_spark.md`:

      - Iceberg extensions enabled
      - Catalog `lake` backed by `org.apache.iceberg.aws.glue.GlueCatalog`
      - Warehouse path read from `STOCK_LAKE_BUCKET_S3` env var
        (e.g. `s3://<your-bucket>/iceberg/`)
      - AWS region from `AWS_REGION` (default `us-east-1`)
      - Adaptive query execution + vectorized Parquet reads enabled

    Local-dev override: set `STOCKALERT_SPARK_LOCAL_MODE=true` and the
    Iceberg JARs get pulled via `spark.jars.packages` so a bare
    `pip install pyspark` works. EMR Serverless / CodeBuild bundle
    the JARs server-side and skip this override.

    Raises:
        RuntimeError: if `STOCK_LAKE_BUCKET_S3` is empty — the
            warehouse path is required and there's no safe default.
        ImportError: if pyspark isn't installed (the `spark` poetry
            group hasn't been enabled).
    """
    warehouse = os.environ.get("STOCK_LAKE_BUCKET_S3", "").strip()
    if not warehouse:
        raise RuntimeError(
            "STOCK_LAKE_BUCKET_S3 is empty — set it to the Iceberg "
            "warehouse root (e.g. s3://<your-bucket>/iceberg/) "
            "before running Spark jobs."
        )

    from pyspark.sql import SparkSession  # lazy: pyspark is optional

    builder = (
        SparkSession.builder.appName(app_name)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(
            f"spark.sql.catalog.{_CATALOG_NAME}",
            "org.apache.iceberg.spark.SparkCatalog",
        )
        .config(
            f"spark.sql.catalog.{_CATALOG_NAME}.catalog-impl",
            "org.apache.iceberg.aws.glue.GlueCatalog",
        )
        .config(f"spark.sql.catalog.{_CATALOG_NAME}.warehouse", warehouse)
        .config(
            f"spark.sql.catalog.{_CATALOG_NAME}.io-impl",
            "org.apache.iceberg.aws.s3.S3FileIO",
        )
        .config(
            "spark.hadoop.fs.s3a.region",
            os.environ.get("AWS_REGION", "us-east-1"),
        )
        .config("spark.sql.parquet.enableVectorizedReader", "true")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
    )

    if os.environ.get("STOCKALERT_SPARK_LOCAL_MODE", "").lower() == "true":
        builder = builder.config(
            "spark.jars.packages",
            "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.6.0,"
            "org.apache.iceberg:iceberg-aws-bundle:1.6.0",
        )

    return builder.getOrCreate()


def record_run(
    *,
    job_name: str,
    status: str,
    rows_written: int = 0,
    symbols_processed: int = 0,
    started_at: float | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Log a structured summary of a Spark job run.

    Phase 1A: emits one JSON line to stdout via the logger so EMR
    Serverless / CodeBuild captures it in CloudWatch alongside Spark's
    own logs. The format is stable so a future commit can route these
    lines into a `spark_runs` CH table (or DataDog) without touching
    every job.

    Returns the dict that was logged so callers / tests can assert on
    it.
    """
    now = time.time()
    duration_s: float | None = None
    if started_at is not None:
        duration_s = round(now - started_at, 3)

    record = {
        "job": job_name,
        "status": status,
        "rows_written": int(rows_written),
        "symbols_processed": int(symbols_processed),
        "duration_s": duration_s,
        "ended_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "error": error,
    }
    if extra:
        record.update(extra)

    line = json.dumps(record, sort_keys=True, default=str)
    if status == "ok":
        logger.info("spark_run %s", line)
    else:
        logger.error("spark_run %s", line)
    return record


__all__ = ["get_spark", "record_run"]
