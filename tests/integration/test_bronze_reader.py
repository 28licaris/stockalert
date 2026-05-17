"""
Pre-Phase 3 Step 2 integration tests — BronzeReader.get_bars(...).

End-to-end against a real Glue catalog + S3 bucket: write a synthetic
day via the (already-tested) BronzeIcebergSink, then read it back via
the production BronzeReader path. Verifies the read path agents and ML
pipelines will use for historical data — provider routing, half-open
intervals, UTC normalization, Pydantic schema fidelity.

To avoid scanning the 2B-row production `polygon_minute`, we redirect
the module's `_PROVIDER_TABLE` mapping to point at a temp table for the
duration of the fixture, then restore. This exercises the real
`get_bars` code without duplicating its logic in the test.

Skips automatically when AWS credentials or `STOCK_LAKE_BUCKET` are
missing, mirroring the other Phase 1+ integration tests.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import date, datetime, timezone
from typing import Iterator

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


def _purge_s3_prefix(s3_uri: str) -> None:
    import boto3

    assert s3_uri.startswith("s3://"), s3_uri
    bucket, _, prefix = s3_uri[len("s3://") :].partition("/")
    if not prefix.endswith("/"):
        prefix += "/"
    s3 = boto3.client("s3", region_name=settings.stock_lake_region)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", []) or []]
        if objs:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objs, "Quiet": True})


def _synthetic_day_df(symbols: list[str], day: date, minutes: int = 30) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        for m in range(minutes):
            rows.append({
                "symbol": sym,
                "timestamp": pd.Timestamp(
                    year=day.year, month=day.month, day=day.day,
                    hour=14, minute=m, tz="UTC",
                ),
                "open": 100.0 + m * 0.01,
                "high": 100.5 + m * 0.01,
                "low":  99.5 + m * 0.01,
                "close": 100.2 + m * 0.01,
                "volume": float(1000 + m),
                "vwap":  0.0,
                "trade_count": 10 + m,
                "source": "polygon",
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def reader_against_temp_table() -> Iterator[tuple]:
    """
    Create a one-off Iceberg table with the polygon_minute shape, seed
    it with one synthetic day for two symbols, and redirect the
    BronzeReader's "polygon" routing to point at this table. Yield
    (table, table_id, reader). Restore the mapping + drop the table on
    teardown.
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
        from app.services.bronze.sink import BronzeIcebergSink
        from app.services.readers import bronze_reader as br_mod
        from app.services.readers.bronze_reader import BronzeReader
    except ImportError as exc:
        pytest.skip(f"required module not installed: {exc}")

    reset_catalog_cache()
    catalog = get_catalog()

    suffix = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    table_name = f"polygon_minute_reader_test_{suffix}"
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

    sink = BronzeIcebergSink(table=table)
    day = date(2024, 8, 1)
    df = _synthetic_day_df(["READA", "READB"], day, minutes=30)
    result = asyncio.run(
        sink.write(df, file_date=day, kind="minute", provider="polygon")
    )
    assert result.status == "ok", f"seed write failed: {result.error}"

    # Redirect provider routing to the temp table for the duration of the fixture.
    original_mapping = br_mod._PROVIDER_TABLE.copy()
    br_mod._PROVIDER_TABLE["polygon"] = table_name

    reader = BronzeReader(catalog)

    try:
        yield table, table_id, reader
    finally:
        # Restore mapping first so a teardown error doesn't leak a half-state.
        br_mod._PROVIDER_TABLE.clear()
        br_mod._PROVIDER_TABLE.update(original_mapping)
        try:
            catalog.drop_table(table_id)
        except Exception:
            pass
        _purge_s3_prefix(location)


def test_get_bars_happy_path(reader_against_temp_table) -> None:
    """Reader returns the bars we wrote, sorted, with Pydantic shape."""
    _, _, reader = reader_against_temp_table
    from app.services.readers.schemas import BronzeBar

    start = datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc)
    end = datetime(2024, 8, 1, 15, 0, tzinfo=timezone.utc)
    bars = reader.get_bars("READA", start, end)

    assert len(bars) == 30, f"expected 30 minute bars, got {len(bars)}"
    assert all(isinstance(b, BronzeBar) for b in bars)
    assert all(b.symbol == "READA" for b in bars)
    for prev, curr in zip(bars, bars[1:]):
        assert prev.timestamp < curr.timestamp
    first = bars[0]
    assert first.open > 0 and first.close > 0
    assert first.volume > 0
    assert first.source == "polygon"


def test_get_bars_empty_window_returns_empty(reader_against_temp_table) -> None:
    """No data in the window → []  (not an exception)."""
    _, _, reader = reader_against_temp_table
    bars = reader.get_bars(
        "READA",
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 1, 2, tzinfo=timezone.utc),
    )
    assert bars == []


def test_get_bars_unknown_symbol_returns_empty(reader_against_temp_table) -> None:
    """Symbol that doesn't exist in the table → []."""
    _, _, reader = reader_against_temp_table
    bars = reader.get_bars(
        "NOTREAL_XYZ",
        datetime(2024, 8, 1, tzinfo=timezone.utc),
        datetime(2024, 8, 2, tzinfo=timezone.utc),
    )
    assert bars == []


def test_get_bars_inverted_window_returns_empty(reader_against_temp_table) -> None:
    """end <= start → []  (no exception)."""
    _, _, reader = reader_against_temp_table
    t = datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc)
    assert reader.get_bars("READA", t, t) == []
    assert reader.get_bars("READA", t, t.replace(hour=13)) == []


def test_get_bars_half_open_interval(reader_against_temp_table) -> None:
    """end is exclusive: requesting [14:00, 14:30) returns exactly the first 30 bars."""
    _, _, reader = reader_against_temp_table
    bars = reader.get_bars(
        "READA",
        datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc),
        datetime(2024, 8, 1, 14, 30, tzinfo=timezone.utc),
    )
    assert len(bars) == 30
    # Last bar is 14:29 (inclusive). A bar at exactly 14:30 would not be included.
    assert bars[-1].timestamp.minute == 29


def test_get_bars_naive_datetime_treated_as_utc(reader_against_temp_table) -> None:
    """A naive datetime is coerced to UTC per the documented contract."""
    _, _, reader = reader_against_temp_table
    bars = reader.get_bars(
        "READA",
        datetime(2024, 8, 1, 14, 0),   # naive
        datetime(2024, 8, 1, 15, 0),   # naive
    )
    assert len(bars) == 30


def test_get_bars_limit_returns_most_recent(reader_against_temp_table) -> None:
    """`limit=N` returns the LAST N bars (most recent), not the first."""
    _, _, reader = reader_against_temp_table
    bars = reader.get_bars(
        "READA",
        datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc),
        datetime(2024, 8, 1, 15, 0, tzinfo=timezone.utc),
        limit=5,
    )
    assert len(bars) == 5
    assert [b.timestamp.minute for b in bars] == [25, 26, 27, 28, 29]


def test_unknown_provider_raises() -> None:
    """Bad provider name is a programmer error, not a data condition."""
    from app.services.readers.bronze_reader import BronzeReader

    reader = BronzeReader.__new__(BronzeReader)  # skip __init__; catalog not used
    with pytest.raises(ValueError, match="Unknown provider"):
        reader.get_bars(
            "ANY",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
            provider="madeup",
        )
