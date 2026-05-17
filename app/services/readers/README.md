# readers/

Read services. One module per data source, all sharing the
[`schemas.py`](schemas.py) Pydantic contract.

These are the **only** read surface meant to be consumed by HTTP
routes, MCP tools, and other services. Routes and MCP tools are thin
adapters over readers — never put business logic in the route layer.

## Why this folder exists

Three goals, in priority order:

1. **CH-independent historical reads.** ML training and agent
   historical queries hit Iceberg directly via `bronze_reader` /
   `silver_reader`. ClickHouse can be down, redeployed, or wiped and
   training reproducibility is unaffected.
2. **Single contract across surfaces.** The same Pydantic shape
   (`BronzeBar`, etc.) flows out of routes and MCP tools. An agent
   that consumes a route response can consume an MCP response with
   zero shape-conversion code.
3. **Liftable services.** Each reader can become its own container
   later — talk to it over HTTP or gRPC, the contract is already in
   `schemas.py`.

## Current contents

| File | Reader | Source | Notes |
|---|---|---|---|
| [schemas.py](schemas.py) | — | — | All Pydantic DTOs. `BronzeBar`, `BronzeBarsResponse`, `LakeSymbolsResponse`, `LakeLatestDayResponse`, `LiveBar`, `LiveBarsResponse`, `LatestBarsResponse`, `Signal`, `SignalsResponse`, `Quote`, `QuotesResponse`. |
| [bronze_reader.py](bronze_reader.py) | `BronzeReader` | Iceberg `bronze.{provider}_minute` | CH-independent. Provider = `polygon` / `schwab`. `get_bars`, `list_symbols`, `latest_trading_day`. |
| [bar_reader.py](bar_reader.py) | `BarReader` | CH `ohlcv_1m` / `ohlcv_5m` / `ohlcv_daily` | Live tier. `get_recent_bars`, `get_bars_in_range` (supports resampled intervals `15m`/`30m`/`1h`/`4h`), `get_latest_bar_per_symbol`. |
| [signal_reader.py](signal_reader.py) | `SignalReader` | CH `signals` | `get_recent_signals`, `get_signals_by_symbol`. |
| [quote_service.py](quote_service.py) | `QuoteService` | Provider REST (Schwab / Polygon — same fallback chain the banner uses) | Async. `get_quote(symbol)`, `get_quotes(symbols)`. Normalizes provider-specific field names into the canonical `Quote` shape. |
| [indicator_reader.py](indicator_reader.py) | `IndicatorReader` | `BronzeReader` / `BarReader` + `INDICATOR_REGISTRY` | Single source of truth for indicator computation across dashboard / MCP / backtester. `get_series(symbol, indicator, params, ...)` and `get_chart_data(symbol, indicator_specs, ...)`. Multi-output indicators (Bollinger / Stochastic / MACD) decompose into multiple `IndicatorSeries` entries per response. See [docs/indicator_exposure_design.md](../../../docs/indicator_exposure_design.md). |

## Planned (Phase 3+)

| File | Reader | Source |
|---|---|---|
| `silver_reader.py` | `SilverReader` | Iceberg `silver.ohlcv_1m` (canonical merged) |
| `feature_reader.py` | `FeatureReader` | Iceberg `gold.features_*` (pre-computed for ML) |

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

Hard rules (see also `app/services/bronze/README.md` for the writer
side):

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
credentials / infra aren't available. The Phase-Pre-3 gate test (Step 2)
verifies `BronzeReader.get_bars(...)` works with ClickHouse stopped.

Unit tests for boundary logic (UTC coercion, half-open interval edges,
unknown providers) can use a tiny temp Iceberg table — see
`tests/integration/test_bronze_sink.py` for the temp-table fixture
pattern.
