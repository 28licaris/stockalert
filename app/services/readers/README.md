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
| [schemas.py](schemas.py) | — | — | `BronzeBar`, `BronzeBarsResponse` |
| [bronze_reader.py](bronze_reader.py) | `BronzeReader` | Iceberg `bronze.{provider}_minute` | CH-independent. Provider = `polygon` / `schwab`. |

## Planned (Step 2 continuation)

| File | Reader | Source |
|---|---|---|
| `bar_reader.py` | `BarReader` | CH `ohlcv_1m` (live tier) |
| `signal_reader.py` | `SignalReader` | CH `signals` |
| `quote_service.py` | `QuoteService` | provider REST (Polygon / Schwab / Alpaca) with same fallback chain the banner uses |
| `silver_reader.py` (Phase 3+) | `SilverReader` | Iceberg `silver.ohlcv_1m` |

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
