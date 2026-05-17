# Legacy services

**Do not extend. Targeted for Phase 6 removal.**

These modules implement the pre-Iceberg "raw S3" lake path. They've
been superseded by the bronze service (`app/services/bronze/`) which
provides catalog metadata, time travel, schema evolution, and Athena
compatibility — none of which the raw/ layout has.

## What lives here

| Module | Purpose | Superseded by |
|---|---|---|
| `lake_archive.py` | `LakeArchiveWriter` — writes `raw/provider=*/kind=*/year=YYYY/date=YYYY-MM-DD.parquet`. | `bronze.BronzeIcebergSink` |
| `lake_sink.py` | `LakeSink` — wraps `LakeArchiveWriter` for the fan-out pattern. | `bronze.BronzeIcebergSink` |
| `s3_lake_client.py` | Thin boto3 transport used only by `lake_archive`. | Direct boto3 / s3fs / PyIceberg |

## Why kept around

1. **Legacy scripts** (`polygon_flatfiles_bulk_backfill.py`,
   `schwab_lake_backfill.py`) and a smoke test (`_smoke_dual_sink.py`)
   still import these for research / one-off raw archive runs.
2. **Existing test coverage** (`test_lake_archive.py`,
   `test_flatfiles_sinks.py`, `test_s3_lake_client.py`) protects the
   contract for the historical `raw/` data still in S3.
3. The user has 5 years of Polygon data in `s3://.../raw/provider=
   polygon-flatfiles/` from the Phase 1 import; the `raw/` writer is
   the only thing that knows that layout if a re-import is ever needed.

## Retirement plan (Phase 6)

When silver-build (Phase 3) and the dashboard reader-flip (Phase 4)
are stable and we've confirmed nothing depends on raw/:

1. Delete the historical `s3://.../raw/` prefix.
2. Delete this whole `legacy/` folder.
3. Drop the `lake_archive_watermarks` ClickHouse table.
4. Remove the legacy script CLIs.
