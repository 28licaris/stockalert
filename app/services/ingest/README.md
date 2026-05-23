# Ingest service

Everything that **puts data into** the hot or cold tier. Multi-provider.

## What lives here

| Module | Purpose |
|---|---|
| `nightly_polygon_refresh.py` | Daily 07:00 UTC: Polygon flat files → `equities.polygon_raw` via `EquitiesIcebergSink.for_polygon_raw()` (CV7). Auto-catches up missed weekdays. |
| `nightly_schwab_refresh.py` | Daily 22:00 UTC: Schwab pricehistory → `equities.schwab_universe` (CV8). Auto-catchup. |
| `corp_actions.py` | `PolygonCorpActionsIngest` — splits + dividends → `equities.market_corp_actions` (CV9). Driver: `scripts/run_corp_actions_backfill.py` (`--full` for one-shot history, `--nightly` for incremental cron). |
| `live_lake_writer.py` | Per-cycle micro-batch writer: live stream → `equities.schwab_universe` so the universe table stays fresh between nightly Schwab refreshes (CV7.5). Idempotent via `ensure_equities_table` on cold-start. |
| `schwab_tip_fill.py` | Stream-warmup gap-fill: Schwab REST → recent bars for newly-added universe members; reads the lake watermark to know where to resume. |
| `lake_to_ch_backfill.py` | Lake → ClickHouse warmup: reads `equities.polygon_adjusted` to hot-load history for symbols entering the live universe. |
| `backfill_service.py` | On-demand REST backfill into ClickHouse (gap-fill for dashboard symbols). |
| `flatfiles_backfill.py` | Polygon flat-files → fan-out sinks (CH + equities lake). Drives the nightly Polygon job. |
| `historical_loader.py` | Wraps `DataProvider.historical_df` with chunking + provider-specific limits. |
| `sinks.py` | `Sink` Protocol + `ClickHouseSink` + `SinkResult`. Used by the flat-files backfill. The equities Iceberg sink lives in `app/services/equities/sink.py` as `EquitiesIcebergSink` (post-CV5/CV7). |

## Contracts other modules import from

- `from app.services.ingest.sinks import Sink, SinkResult, ClickHouseSink, Kind`

## Cadence

Both nightly jobs run as asyncio background tasks armed in `app/main_api.py`
startup. Each is gated by its own `*_NIGHTLY_ENABLED` env var and Schwab
credentials (where applicable). One service failing does not affect the
other — startup wraps each in defensive try/except.

The corp-actions backfill (`run_corp_actions_backfill.py --nightly`) is
run as a separate cron, not from uvicorn startup — it has its own
write-verify contract and needs explicit operator visibility.
