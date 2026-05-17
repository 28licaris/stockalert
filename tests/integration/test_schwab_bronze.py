"""
Phase 2 integration test — Schwab variant of `BronzeIcebergSink`.

Mirrors `test_bronze_sink.py` but exercises the schwab_minute schema and
the `for_schwab_minute()` factory's provider-filter (accepts schwab,
rejects polygon).

Uses a TEMP Iceberg table so we don't touch `bronze.schwab_minute`.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import date

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
def temp_schwab_bronze_table():
    if not settings.stock_lake_bucket:
        pytest.skip("STOCK_LAKE_BUCKET unset")
    if not _aws_present():
        pytest.skip("AWS credentials not present")

    try:
        from app.services.iceberg_catalog import get_catalog, reset_catalog_cache
        from app.services.bronze.schemas import (
            BRONZE_SCHWAB_MINUTE_SCHEMA,
            BRONZE_SCHWAB_MINUTE_PARTITION,
            BRONZE_SCHWAB_MINUTE_SORT,
        )
    except ImportError as exc:
        pytest.skip(f"pyiceberg / bronze module not installed: {exc}")

    reset_catalog_cache()
    catalog = get_catalog()

    suffix = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    table_name = f"schwab_minute_sink_test_{suffix}"
    table_id = f"{settings.iceberg_glue_database}.{table_name}"
    warehouse = f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}"
    location = f"{warehouse}/bronze/{table_name}"

    table = catalog.create_table(
        identifier=table_id,
        schema=BRONZE_SCHWAB_MINUTE_SCHEMA,
        location=location,
        partition_spec=BRONZE_SCHWAB_MINUTE_PARTITION,
        sort_order=BRONZE_SCHWAB_MINUTE_SORT,
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
    without_scheme = s3_uri[len("s3://"):]
    bucket, _, prefix = without_scheme.partition("/")
    if not prefix.endswith("/"):
        prefix += "/"
    s3 = boto3.client("s3", region_name=settings.stock_lake_region)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", []) or []]
        if objs:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objs, "Quiet": True})


def _schwab_synthetic_day(symbols: list[str], day: date) -> pd.DataFrame:
    """Build a small canonical-shape DataFrame mimicking Schwab REST output.

    Schwab does not populate vwap or trade_count, so those are NaN here.
    """
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
                "vwap":  pd.NA,         # Schwab doesn't return vwap
                "trade_count": pd.NA,   # Schwab doesn't return trade counts
                "source": "schwab",
            })
    return pd.DataFrame(rows)


def test_schwab_sink_writes_and_reads_back(temp_schwab_bronze_table) -> None:
    """Happy-path: write a synthetic day, scan it back."""
    from app.services.bronze.sink import BronzeIcebergSink
    from pyiceberg.expressions import EqualTo

    table, _ = temp_schwab_bronze_table
    sink = BronzeIcebergSink(
        table=table,
        name="bronze_schwab_minute_test",
        accepted_providers={("schwab", "minute"), ("schwab-rest", "minute")},
    )

    day = date(2024, 7, 15)
    symbols = ["TSCHWAB_A", "TSCHWAB_B"]
    df = _schwab_synthetic_day(symbols, day)
    expected_rows = len(df)

    result = asyncio.run(
        sink.write(df, file_date=day, kind="minute", provider="schwab")
    )

    assert result.status == "ok", f"unexpected status: {result.status} / err: {result.error}"
    assert result.bars_written == expected_rows
    assert result.metadata["snapshot_id_after"] is not None

    table.refresh()
    arrow = table.scan(row_filter=EqualTo("symbol", "TSCHWAB_A")).to_arrow()
    assert arrow.num_rows == 30
    pdf = arrow.to_pandas()
    assert (pdf["source"] == "schwab").all()
    # Schwab doesn't supply vwap/trade_count → must remain NULL in bronze
    assert pdf["vwap"].isna().all()
    assert pdf["trade_count"].isna().all()


def test_schwab_factory_rejects_polygon_provider(temp_schwab_bronze_table) -> None:
    """The for_schwab_minute() factory accepts schwab only — polygon → skipped."""
    from app.services.bronze.sink import BronzeIcebergSink

    table, _ = temp_schwab_bronze_table
    sink = BronzeIcebergSink(
        table=table,
        name="bronze_schwab_minute_test",
        accepted_providers={("schwab", "minute"), ("schwab-rest", "minute")},
    )

    df = _schwab_synthetic_day(["TSCHWAB_A"], date(2024, 7, 16))
    result = asyncio.run(
        sink.write(df, file_date=date(2024, 7, 16), kind="minute", provider="polygon")
    )
    assert result.status == "skipped"
    assert "unsupported" in result.metadata.get("reason", "")


def test_schwab_sink_drops_null_symbol_rows(temp_schwab_bronze_table) -> None:
    """Same data-quality filter applies to Schwab data too."""
    from app.services.bronze.sink import BronzeIcebergSink

    table, _ = temp_schwab_bronze_table
    sink = BronzeIcebergSink(
        table=table,
        name="bronze_schwab_minute_test",
        accepted_providers={("schwab", "minute")},
    )

    day = date(2024, 7, 17)
    df = _schwab_synthetic_day(["TSCHWAB_A"], day)
    bad = df.head(3).copy()
    bad["symbol"] = None
    df = pd.concat([df, bad], ignore_index=True)

    result = asyncio.run(
        sink.write(df, file_date=day, kind="minute", provider="schwab")
    )
    assert result.status == "ok"
    assert result.bars_written == 30
    assert result.metadata["rows_dropped_null_symbol"] == 3
