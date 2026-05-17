# Ingest service

Everything that **puts data into** the hot or cold tier. Multi-provider.

## What lives here

| Module | Purpose |
|---|---|
| `nightly_polygon_refresh.py` | Daily 07:00 UTC: Polygon flat files → `bronze.polygon_minute`. Auto-catches up missed weekdays. |
| `nightly_schwab_refresh.py` | Daily 22:00 UTC: Schwab pricehistory → `bronze.schwab_minute`. Auto-catchup. |
| `backfill_service.py` | On-demand REST backfill into ClickHouse (gap-fill for dashboard symbols). |
| `flatfiles_backfill.py` | Polygon flat-files → fan-out sinks (CH + bronze). Drives the nightly job. |
| `historical_loader.py` | Wraps `DataProvider.historical_df` with chunking + provider-specific limits. |
| `sinks.py` | `Sink` Protocol + `ClickHouseSink` + `SinkResult`. Used by the flat-files backfill and by `BronzeIcebergSink` (which lives in the bronze service). |

## Contracts other modules import from

- `from app.services.ingest.sinks import Sink, SinkResult, ClickHouseSink, Kind`

## Cadence

Both nightly jobs run as asyncio background tasks armed in `app/main_api.py`
startup. Each is gated by its own `*_NIGHTLY_ENABLED` env var and Schwab
credentials (where applicable). One service failing does not affect the
other — startup wraps each in defensive try/except.
