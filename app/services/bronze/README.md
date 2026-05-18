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

### Two data types in bronze

Bronze holds **two kinds of data**, both raw, both per-provider:

1. **OHLCV bars** (continuous time-series): `bronze.polygon_minute`,
   `bronze.schwab_minute`. One row per `(symbol, minute)`; minute prices
   + volume. Updated nightly (Polygon flat-files) + live (Schwab stream).
2. **Corp-actions** (discrete event archive): `bronze.polygon_corp_actions`.
   One row per `(symbol, ex_date, action_type)`; split factors + dividend
   amounts. Updated nightly via Polygon REST. **NOT included in the
   minute-bar flat-files** — these are published separately by Polygon
   and need their own ingest path. Consumed by the silver OHLCV build
   to compute split/dividend-adjusted columns.

Without bronze corp-actions, backtests on stocks with splits would
show fake -75% candles on split day. They're a reference table, not
a time-series — different cadence, different volume, different
partition strategy.

### Tables

Current:
- `stock_lake.polygon_minute` — Polygon flat-file + REST minute bars.
  Partition `month(timestamp)`, sort `(symbol, timestamp)`.
- `stock_lake.schwab_minute` — Schwab REST pricehistory minute bars.
  Same schema and partitioning. Differs at runtime: `vwap` and
  `trade_count` are always null (Schwab doesn't return them).
- `stock_lake.polygon_corp_actions` — Polygon REST splits + dividends
  archive. Partition `year(ex_date)`, sort `(symbol, ex_date)`.
  Identifier `(symbol, ex_date, action_type)` enables idempotent
  re-ingestion via Iceberg upsert. Consumed by `silver_corp_actions_build`.

Planned (created when the first writer for each is added):
- `stock_lake.polygon_day`
- `stock_lake.schwab_day`
- `stock_lake.alpaca_minute`
- `stock_lake.{provider}_corp_actions` — additional providers if/when added.

### Adding / removing providers — what changes here, what doesn't

The architecture is **provider-pluggable** by design (silver_layer_plan
§2.3). Adding a new provider is purely additive at this layer:

1. New schema + `ensure_*` function in this package.
2. New ingest module under `app/services/silver/{kind}/` for the
   new provider.
3. Add the provider name to `SILVER_PROVIDER_PRECEDENCE` env var.

**Zero changes to silver build code.** Silver iterates the
precedence list and silently skips providers whose bronze table
doesn't exist. Removing a provider is the reverse: stop its ingest
job, drop the name from the precedence env var, optionally drop the
table.

### When a provider subscription pauses (the universe-expansion reminder)

If a provider subscription is going to be paused (e.g. Polygon
ending), **expand the universe of any live-streaming providers
BEFORE the pause** — those will be the only sources of fresh data
during the pause window.

Specifically for the canonical case (Polygon paused, Schwab still
streaming our 100-symbol seed universe):
- Polygon flat-files = whole market while active; static archive
  during pause.
- Schwab stream = only seed-universe symbols, ongoing forever.
- During a Polygon pause, **only seed-universe symbols get new
  bronze data.** Non-seed symbols are frozen at the Polygon-pause
  date.

To avoid leaving symbols you'll later want stuck at the pause-date
boundary, run pre-pause:
```bash
poetry run python scripts/promote_to_seed.py --universe sp500
# or --universe russell1000 / --universe russell3000
```

Full runbook in [silver_layer_plan §9.7](../../../docs/silver_layer_plan.md).

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
