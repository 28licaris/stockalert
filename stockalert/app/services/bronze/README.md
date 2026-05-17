# Bronze service

Iceberg tables holding **per-provider, immutable raw market bars**.
Layer 1 of the medallion architecture (bronze → silver → gold).

## What it owns

- **Schemas** for every `bronze.{provider}_{kind}` Iceberg table
  ([schemas.py](schemas.py)).
- **Idempotent table creation** against the Glue catalog
  ([tables.py](tables.py)).
- **`BronzeIcebergSink`** — the canonical writer ([sink.py](sink.py)).
  Drop-in replacement for `LakeSink` in the fan-out pattern from
  `app.services.flatfiles_sinks`. Consumes the same canonical
  DataFrame; writes to Iceberg via `overwrite` filtered to the target
  trading day (idempotent re-runs).

What it does NOT own:
- Live streaming (in [live/monitor_service.py](../live/monitor_service.py) +
  [batcher.py](../../db/batcher.py)).
- Flat-file ingestion logic (in
  [flatfiles_backfill.py](../flatfiles_backfill.py)).
- Catalog connection (in [iceberg_catalog.py](../iceberg_catalog.py)).

## Tables

Current:
- `stock_lake.polygon_minute` — Polygon flat-file + REST minute bars.
  Partition `month(timestamp)`, sort `(symbol, timestamp)`.
- `stock_lake.schwab_minute` — Schwab REST pricehistory minute bars.
  Same schema and partitioning. Differs at runtime: `vwap` and
  `trade_count` are always null (Schwab doesn't return them).

Planned (created when the first writer for each is added):
- `stock_lake.polygon_day`
- `stock_lake.schwab_day`
- `stock_lake.alpaca_minute`

Naming note: Glue databases are flat — no real `bronze.` namespace.
We use a `bronze/` subfolder in the S3 warehouse path
(`s3://${bucket}/iceberg/bronze/<table>/`) so the on-disk layout still
reflects the medallion, but the catalog table name is unqualified
(`stock_lake.polygon_minute`).

## Schema (polygon_minute)

Mirrors the existing on-disk Polygon flat-file Parquet schema so
`add_files` can register the 1,325 historical daily Parquets without
rewriting. The only non-source fields are nullable trailing columns
the new sink will populate; legacy rows have nulls.

| Field | Type | Required | Notes |
|---|---|---|---|
| symbol | string | yes | identifier + sort key |
| timestamp | timestamptz | yes | identifier + sort key (UTC) |
| open / high / low / close | double | no | |
| volume | double | no | fractional in newer files |
| vwap | double | no | placeholder `0.0` in flat files; future writers emit null |
| trade_count | long | no | |
| source | string | no | `"polygon-flatfiles"` for imported; `"polygon"` for live |
| ingestion_ts | timestamptz | no | when row landed; null for imported rows |
| ingestion_run_id | string | no | FK to CH `ingestion_runs`; null for imported rows |

Identifier fields are `(symbol, timestamp)` — used by `MERGE INTO` for
idempotency.

## Usage

```python
from app.services.bronze import BronzeIcebergSink

polygon_sink = BronzeIcebergSink.for_polygon_minute()
schwab_sink  = BronzeIcebergSink.for_schwab_minute()

# Async write — same Sink protocol as ClickHouseSink/LakeSink
result = await schwab_sink.write(df, file_date=date, kind="minute", provider="schwab")
```

## Tests

```bash
# (Phase 1 will add integration tests under tests/integration/bronze/)
poetry run pytest tests/integration/bronze/ -v
```
