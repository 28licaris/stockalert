# 05 — Provider Abstraction

The provider abstraction lets the live tier swap data sources without
rewriting the architecture. Today: Schwab. Tomorrow: Alpaca, Yahoo, or
something not invented yet.

## The interface

```python
# app/providers/base.py — already exists in v1; unchanged for v2.
from abc import ABC, abstractmethod

class DataProvider(ABC):
    """Live + REST historical interface used by the ingest hot path."""

    @abstractmethod
    def start_stream(self) -> None: ...

    @abstractmethod
    def stop_stream(self) -> None: ...

    @abstractmethod
    def subscribe_bars(self, callback: Callable, tickers: list[str]) -> None:
        """Register a callback for new bars + subscribe to tickers."""

    @abstractmethod
    def unsubscribe_bars(self, tickers: list[str]) -> None: ...

    @abstractmethod
    async def historical_df(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min",
    ) -> pd.DataFrame:
        """Return bars in [start, end) at the requested timeframe."""

    async def search_instruments(self, query: str, *, limit: int = 10) -> list[dict]:
        """Optional — default returns []. Override for symbol autocomplete."""
        return []
```

The `StreamService` uses this interface; concrete providers implement
it. `get_stream_provider()` in `app/config.py` selects one based on
the `DATA_PROVIDER` env var.

## Today's providers in v1

| Provider | File | Role | Notes |
|---|---|---|---|
| **Schwab** | `app/providers/schwab_provider.py` | Primary live source | OAuth2 + WebSocket + REST `/pricehistory` |
| **Polygon flat-files** | `app/providers/polygon_flatfiles.py` | Lake bulk loads | S3-resident CSV.gz files; not via DataProvider — operator script-driven |
| **Polygon REST corp-actions** | `app/providers/polygon_corp_actions.py` | Splits + dividends | Whole-market; not via DataProvider |
| Alpaca | not implemented | Future | Free with paper account; covers 1-min × ~6y |

## v2: which provider does what

| Use case | Provider | Why |
|---|---|---|
| **Live WebSocket stream** | Schwab | Free with brokerage; reliable; already implemented |
| **On-add 48d 1-min** | Schwab REST `/pricehistory` | Same auth as the WebSocket; trusted by us |
| **On-add 20y daily** | Schwab REST `/pricehistory` | Schwab daily history goes back 20+ years |
| **Symbol autocomplete** | Schwab REST `/instruments` | Already in use |
| **Whole-market historical bulk** | Polygon flat-files | The only API designed for this — one file = whole market for a day |
| **Whole-market corp_actions** | Polygon REST corp-actions endpoint | Comprehensive split/div coverage |
| **Recent symbol autocomplete fallback** | (future) Alpaca / Yahoo | Survives Schwab outage |

## Adding a new provider

Step-by-step for adding Alpaca as a fallback:

### 1. Implement the provider class

```python
# app/providers/alpaca_provider.py
from app.providers.base import DataProvider

class AlpacaProvider(DataProvider):
    @classmethod
    def from_settings(cls) -> "AlpacaProvider":
        from app.config import settings
        return cls(
            api_key_id=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_secret_key,
        )

    def subscribe_bars(self, callback, tickers):
        # alpaca-py SDK websocket setup ...
        ...

    async def historical_df(self, symbol, start, end, timeframe="1Min"):
        # alpaca-py REST call ...
        ...
```

### 2. Register in config

```python
# app/config.py
def get_stream_provider() -> DataProvider:
    name = settings.stream_provider.lower()
    if name == "schwab":
        from app.providers.schwab_provider import SchwabProvider
        return SchwabProvider.from_settings()
    if name == "alpaca":
        from app.providers.alpaca_provider import AlpacaProvider
        return AlpacaProvider.from_settings()
    raise ValueError(f"unknown STREAM_PROVIDER={name!r}")
```

### 3. Env vars

```
ALPACA_API_KEY_ID=...
ALPACA_SECRET_KEY=...
STREAM_PROVIDER=alpaca   # to switch
```

### 4. Tests

The interface contract gives you a free testing surface — any tests
that work with `SchwabProvider` (mocked) also work with `AlpacaProvider`
(mocked). Tests live in `tests/providers/test_alpaca_provider.py`.

### 5. Restart uvicorn

`StreamService.start()` calls `get_stream_provider()` once at startup.
A restart switches providers cleanly.

## Multi-provider fallback (future)

In v1 there's one live provider at a time. A future enhancement:
`MultiProviderStreamService` that tries Schwab first, falls back to
Alpaca if Schwab WS dies.

Skeleton sketch:

```python
class MultiProviderStreamService:
    def __init__(self, primary: DataProvider, fallbacks: list[DataProvider]):
        ...

    def subscribe_bars(self, callback, tickers):
        try:
            self.primary.subscribe_bars(callback, tickers)
        except Exception:
            logger.warning("primary down, switching to fallback")
            self.active = self.fallbacks[0]
            self.active.subscribe_bars(callback, tickers)
```

Caveat: bar shapes may differ across providers (timestamp alignment,
asset_type strings, exchange names). Normalize at the provider's
edge so downstream code stays single-shape.

## When Polygon subscription ends

Polygon is NOT on the live critical path in v2 (Schwab covers
everything live). Polygon dependencies:

- `equities.polygon_raw` — frozen at the last refresh date; queryable forever
- `equities.polygon_adjusted` — frozen at the last adjustment-job run
- `equities.market_corp_actions` — frozen; new splits don't get ingested
- Polygon flat-files job — stops working (no source)
- Polygon REST `/aggs` (if used) — stops working

**What still works:**
- Live charting (Schwab only)
- On-add for any symbol Schwab covers (most US equities)
- ML training reads of existing snapshots
- Backtests against `polygon_adjusted` snapshots

**What degrades:**
- New symbols added post-Polygon-end can only get Schwab's 48d 1-min
  + 20y daily. The 5y of 1-min from Polygon is not available for
  symbols you didn't already stream.
- Corp_actions stop updating. New splits won't be applied to
  `polygon_adjusted` until you ingest a new corp_actions source
  (Yahoo, Alpha Vantage, etc.).

**Mitigation if you want to keep Polygon-less long-term:**
- Add Alpaca as the historical 1-min source (6 years free with paper account).
- Add Yahoo as a corp_actions source (free, less comprehensive than Polygon).
- Keep the existing 5y Polygon snapshot in S3 — it's an immutable archive.

## Schwab outage runbook

The most likely "thing breaks" — Schwab OAuth token expires (default
7-day lifetime). Operator runbook:

```bash
# 1. Refresh token (browser OAuth)
cd /path/to/stockalert
poetry run python scripts/schwab_get_refresh_token.py
# Browser opens; sign in; paste the returned URL.

# 2. Restart uvicorn (so it re-reads the .env / token file)
pkill -TERM -f 'uvicorn app.main_api'
poetry run uvicorn app.main_api:app --reload

# 3. Verify
curl http://localhost:8000/api/v1/stream/status
# expect: provider="schwab", provider_ready=true
```

See [07_runbook.md](07_runbook.md) for the full operator procedures.

## Lake-side providers (no DataProvider abstraction)

The Polygon flat-files ingest and corp-actions ingest are
**operator scripts**, not live-tier streaming providers. They sit
outside the `DataProvider` interface because they're batch ETL
jobs, not streaming sources.

These live under `app/services/ingest/` (or in v2, the Spark scripts
under `scripts/spark/`). The interface for them is:
- CLI argument signature
- Idempotent on Iceberg
- Records to `ingestion_runs` for audit

If you want to add a new bulk-history provider (e.g. Tiingo, IEX),
add it as a Spark script — not as a `DataProvider`.

## See also

- [01_architecture.md](01_architecture.md) — where providers fit in the system
- [04_spark.md](04_spark.md) — batch-job providers (Polygon ETL)
- [07_runbook.md](07_runbook.md) — operator procedures including Schwab OAuth refresh
