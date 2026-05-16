"""
Phase 1 integration test — BronzeIcebergSink end-to-end against a real
Glue catalog + S3 bucket.

Uses a TEMP Iceberg table so we don't touch the production
`bronze.polygon_minute` (which holds 2.1B rows). The temp table is
created from the same schema + partition spec + sort order via
PyIceberg, written to, read back, then dropped — including the data
files under its location.

Skips automatically if AWS / Iceberg infra is not configured (same
gate-check as the connectivity test).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.config import settings


pytestmark = pytest.mark.integration


def _aws_present() -> bool:
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        return True
    if os.getenv("AWS_PROFILE"):
        return True
    return os.path.isfile(os.path.expanduser("~/.aws/credentials"))


@pytest.fixture(scope="module")
def temp_bronze_table():
    """
    Create a one-off Iceberg table with the same shape as
    `bronze.polygon_minute`, give it back to the test, and drop it
    (data + metadata) afterwards.
    """
    if not settings.stock_lake_bucket:
        pytest.skip("STOCK_LAKE_BUCKET unset")
    if not _aws_present():
        pytest.skip("AWS credentials not present")

    try:
        from app.services.iceberg_catalog import get_catalog, reset_catalog_cache
        from app.services.bronze.schemas import (
            BRONZE_POLYGON_MINUTE_SCHEMA,
            BRONZE_POLYGON_MINUTE_PARTITION,
            BRONZE_POLYGON_MINUTE_SORT,
        )
    except ImportError as exc:
        pytest.skip(f"pyiceberg / bronze module not installed: {exc}")

    reset_catalog_cache()
    catalog = get_catalog()

    suffix = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    table_name = f"polygon_minute_sink_test_{suffix}"
    table_id = f"{settings.iceberg_glue_database}.{table_name}"
    warehouse = f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}"
    location = f"{warehouse}/bronze/{table_name}"

    table = catalog.create_table(
        identifier=table_id,
        schema=BRONZE_POLYGON_MINUTE_SCHEMA,
        location=location,
        partition_spec=BRONZE_POLYGON_MINUTE_PARTITION,
        sort_order=BRONZE_POLYGON_MINUTE_SORT,
        properties={
            "write.target-file-size-bytes": str(64 * 1024 * 1024),
            "write.parquet.compression-codec": "snappy",
        },
    )
    try:
        yield table, table_id
    finally:
        try:
            catalog.drop_table(table_id)
        except Exception:
            pass
        _purge_s3_prefix(location)


def _purge_s3_prefix(s3_uri: str) -> None:
    import boto3

    assert s3_uri.startswith("s3://"), s3_uri
    without_scheme = s3_uri[len("s3://") :]
    bucket, _, prefix = without_scheme.partition("/")
    if not prefix.endswith("/"):
        prefix += "/"
    s3 = boto3.client("s3", region_name=settings.stock_lake_region)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", []) or []]
        if objs:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objs, "Quiet": True})


def _synthetic_day_df(symbols: list[str], day: date) -> pd.DataFrame:
    """Build a small canonical-shape DataFrame for one trading day."""
    rows = []
    for sym in symbols:
        for minute in range(0, 30):
            rows.append({
                "symbol": sym,
                "timestamp": pd.Timestamp(year=day.year, month=day.month, day=day.day,
                                          hour=14, minute=minute, tz="UTC"),
                "open": 100.0 + minute * 0.01,
                "high": 100.5 + minute * 0.01,
                "low":  99.5 + minute * 0.01,
                "close": 100.2 + minute * 0.01,
                "volume": float(1000 + minute),
                "vwap":  0.0,  # placeholder, sink converts to null
                "trade_count": 10 + minute,
                "source": "polygon",
            })
    return pd.DataFrame(rows)


def test_bronze_sink_writes_and_reads_back(temp_bronze_table) -> None:
    """Happy-path: write a synthetic day, scan it back via Iceberg."""
    from app.services.bronze.sink import BronzeIcebergSink
    from pyiceberg.expressions import EqualTo

    table, table_id = temp_bronze_table
    sink = BronzeIcebergSink(table=table)

    day = date(2024, 7, 15)
    symbols = ["TESTA", "TESTB", "TESTC"]
    df = _synthetic_day_df(symbols, day)
    expected_rows = len(df)

    result = asyncio.run(
        sink.write(df, file_date=day, kind="minute", provider="polygon")
    )

    assert result.status == "ok", f"unexpected status: {result.status} / err: {result.error}"
    assert result.bars_written == expected_rows
    assert result.metadata["ingestion_run_id"]
    assert result.metadata["snapshot_id_after"] is not None
    assert result.metadata["rows_dropped_null_symbol"] == 0
    assert result.metadata["rows_dropped_null_ts"] == 0

    # Read back: filter on a known symbol; partition prune kicks in so this is fast.
    table.refresh()
    arrow = table.scan(row_filter=EqualTo("symbol", "TESTA")).to_arrow()
    assert arrow.num_rows == 30  # 30 minutes per symbol
    pdf = arrow.to_pandas()
    assert (pdf["source"] == "polygon").all()
    assert pdf["vwap"].isna().all(), "vwap=0.0 placeholders should become NULL"
    assert pdf["ingestion_run_id"].notna().all()
    assert pdf["ingestion_ts"].notna().all()


def test_bronze_sink_drops_null_symbol_rows(temp_bronze_table) -> None:
    """Data-quality filter: NULL symbol rows are rejected at the sink boundary."""
    from app.services.bronze.sink import BronzeIcebergSink

    table, _ = temp_bronze_table
    sink = BronzeIcebergSink(table=table)

    day = date(2024, 7, 16)
    df = _synthetic_day_df(["TESTA"], day)
    # Inject a few rows with NULL symbol
    bad = df.head(3).copy()
    bad["symbol"] = None
    df = pd.concat([df, bad], ignore_index=True)

    result = asyncio.run(
        sink.write(df, file_date=day, kind="minute", provider="polygon")
    )

    assert result.status == "ok"
    assert result.bars_written == 30  # 30 valid rows, 3 NULL rows dropped
    assert result.metadata["rows_dropped_null_symbol"] == 3


def test_bronze_sink_unsupported_provider_skips(temp_bronze_table) -> None:
    """A sink configured with an `accepted_providers` set rejects others."""
    from app.services.bronze.sink import BronzeIcebergSink

    table, _ = temp_bronze_table
    sink = BronzeIcebergSink(
        table=table,
        accepted_providers={("polygon", "minute"), ("polygon-flatfiles", "minute")},
    )

    df = _synthetic_day_df(["TESTA"], date(2024, 7, 17))
    result = asyncio.run(
        sink.write(df, file_date=date(2024, 7, 17), kind="day", provider="polygon")
    )
    assert result.status == "skipped"
    assert "unsupported" in result.metadata.get("reason", "")


def test_bronze_sink_empty_frame_skips(temp_bronze_table) -> None:
    """Empty input returns skipped, not error."""
    from app.services.bronze.sink import BronzeIcebergSink

    table, _ = temp_bronze_table
    sink = BronzeIcebergSink(table=table)

    result = asyncio.run(
        sink.write(pd.DataFrame(), file_date=date(2024, 7, 18), kind="minute", provider="polygon")
    )
    assert result.status == "skipped"
    assert result.metadata.get("reason") == "empty_frame"
