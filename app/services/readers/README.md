# readers/

Read services. One module per data source, all sharing the
[`schemas.py`](schemas.py) Pydantic contract.

These are the **only** read surface meant to be consumed by HTTP
routes, MCP tools, and other services. Routes and MCP tools are thin
adapters over readers — never put business logic in the route layer.

## Why this folder exists

Three goals, in priority order:

1. **CH-independent historical reads.** ML training and agent
   historical queries hit Iceberg directly via the v2 lake readers
   (`BronzeReader` → `equities.polygon_raw` / `equities.schwab_universe`;
   `AdjustedOhlcvReader` → `equities.polygon_adjusted` + optional
   `equities.schwab_universe` UNION). ClickHouse can be down,
   redeployed, or wiped and training reproducibility is unaffected.
2. **Single contract across surfaces.** The same Pydantic shape
   (`BronzeBar`, `SilverBar`, etc.) flows out of routes and MCP
   tools. An agent that consumes a route response can consume an MCP
   response with zero shape-conversion code.
3. **Liftable services.** Each reader can become its own container
   later — talk to it over HTTP or gRPC, the contract is already in
   `schemas.py`.

## Current contents

| File | Reader | Source | Notes |
|---|---|---|---|
| [schemas.py](schemas.py) | — | — | All Pydantic DTOs. Lake-history: `BronzeBar`/`BronzeBarsResponse`, `SilverBar`/`SilverBarsResponse` (CV11 name carry-overs — actually adjusted bars; see schemas.py section header), `CorpActionsResponse`, `LakeSymbolsResponse`, `LakeLatestDayResponse`, `LakeSnapshot`/`LakeSnapshotsResponse`, `AdjustedSymbolsResponse`, `SourceCoverage`/`SymbolCoverageResponse`, `CrossProviderDiffRow`/`CrossProviderDiffResponse`, `BarQualityRow`/`BarQualityResponse` (empty-only until v2 has a quality table). Live tier: `LiveBar`/`LiveBarsResponse`, `LatestBarsResponse`, `Signal`/`SignalsResponse`. Discovery + observability: `WatchlistSummary`/`WatchlistDetail`/`WatchlistsResponse`, `CoverageReport`, `IntradayGap`/`GapReport`, `BronzeTableStats`, `LakeFreshnessReport`, `ServiceStatus`/`SystemHealthReport`. Quotes: `Quote`/`QuotesResponse`. Indicators: `IndicatorValue`/`IndicatorSeries`/`IndicatorChartData`. |
| [bronze_reader.py](bronze_reader.py) | `BronzeReader` | Iceberg `equities.polygon_raw` (provider=polygon) or `equities.schwab_universe` (provider=schwab) | CH-independent. Class name kept from v1 for caller stability — actually reads v2 equities tables (see file docstring). `get_bars`, `list_symbols`, `latest_trading_day`. |
| [adjusted_ohlcv_reader.py](adjusted_ohlcv_reader.py) | `AdjustedOhlcvReader` | Iceberg `equities.polygon_adjusted` (deep history, split-adjusted by the Spark job) + optional UNION with `equities.schwab_universe` when `include_live=True` (CV25) | CH-independent. `get_bars`, `get_bar_quality` (returns empty — no v2 quality table yet). Canonical post-CV11 reader for adjusted OHLCV. |
| [corp_actions_reader.py](corp_actions_reader.py) | `CorpActionsReader` | Iceberg `equities.market_corp_actions` | CH-independent. `get_corp_actions(symbol, since, until, action_types)`. Backed by Polygon REST nightly + on-demand backfills via `scripts/run_corp_actions_backfill.py`. |
| [lake_metadata_reader.py](lake_metadata_reader.py) | `LakeMetadataReader` | Lake-wide metadata across `equities.*` Iceberg tables | CH-independent. `lake_snapshot_list` etc. (CV29). |
| [bar_reader.py](bar_reader.py) | `BarReader` | CH `ohlcv_1m` / `ohlcv_5m` / `ohlcv_daily` | Live tier. `get_recent_bars`, `get_bars_in_range` (supports resampled intervals `15m`/`30m`/`1h`/`4h`), `get_latest_bar_per_symbol`. |
| [signal_reader.py](signal_reader.py) | `SignalReader` | CH `signals` | `get_recent_signals`, `get_signals_by_symbol`. |
| [quote_service.py](quote_service.py) | `QuoteService` | Provider REST (Schwab / Polygon — same fallback chain the banner uses) | Async. `get_quote(symbol)`, `get_quotes(symbols)`. Normalizes provider-specific field names into the canonical `Quote` shape. |
| [indicator_reader.py](indicator_reader.py) | `IndicatorReader` | `BronzeReader` / `BarReader` + `INDICATOR_REGISTRY` | Single source of truth for indicator computation across dashboard / MCP / backtester. `get_series(symbol, indicator, params, ...)` and `get_chart_data(symbol, indicator_specs, ...)`. Multi-output indicators (Bollinger / Stochastic / MACD) decompose into multiple `IndicatorSeries` entries per response. See [docs/indicator_exposure_design.md](../../../docs/indicator_exposure_design.md). |

## Possible future readers

A pre-computed-feature reader (analogue to "gold features" in the v1
medallion model) is a likely follow-on for ML training pipelines. The
v2 lake has no dedicated feature surface yet — features today are
computed on-demand inside `IndicatorReader`. When a materialized
feature store lands, it'll attach to a sibling `equities.features_*`
table (or a separate Glue database) and get its own reader here.

## Contract

Every reader follows the same shape:

```python
class XReader:
    def __init__(self, *dependencies): ...

    @classmethod
    def from_settings(cls) -> "XReader":
        """Production construction path — reads app.config.settings."""

    def get_<thing>(self, ...) -> list[<SchemaDTO>] | <SchemaDTO> | None:
        """
        Pure read. No writes. No side effects beyond logging.
        Empty result -> [] or None; bad input (unknown provider,
        invalid range) -> ValueError; transient infra failure ->
        retry-friendly exception bubbled up.
        """
```

Hard rules (the writer-side counterpart lives at
[`app/services/equities/`](../equities/) — `EquitiesIcebergSink` in
[`sink.py`](../equities/sink.py)):

- **No `settings` reads inside the reader methods.** Inject via
  constructor or `from_settings()`. Keeps unit tests trivial.
- **Pure functions on the request.** Same `(symbol, start, end)` →
  same bars. No hidden mutable state, no per-call caching unless
  documented in the contract.
- **Half-open intervals.** `[start, end)` everywhere. Avoids
  off-by-one at day boundaries.
- **UTC at the boundary.** Datetimes accepted in any tz, normalized
  to UTC internally, returned as tz-aware UTC.
- **Errors that are programming bugs raise.** Errors that are data
  conditions return empty / `None`. Unknown provider → `ValueError`;
  no rows in window → `[]`.

## How to test

Integration tests live in [tests/integration/](../../../tests/integration/).
They run against real Iceberg + real ClickHouse and skip gracefully when
credentials / infra aren't available.

Unit tests for boundary logic (UTC coercion, half-open interval edges,
unknown providers) can use a tiny temp Iceberg table — see
`tests/integration/test_iceberg_connectivity.py` for the temp-table
fixture pattern.
