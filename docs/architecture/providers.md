# Provider abstraction

How StockAlert swaps data sources without rewriting the ingest path. The system
map is in [`README.md`](README.md).

---

## 1. The interface

```python
# app/providers/base.py
class DataProvider(ABC):
    @abstractmethod
    def start_stream(self): ...
    @abstractmethod
    def stop_stream(self): ...
    @abstractmethod
    def subscribe_bars(self, callback, tickers: list[str]): ...
    @abstractmethod
    def unsubscribe_bars(self, tickers: list[str]): ...
    @abstractmethod
    async def historical_df(
        self, symbol: str, start, end, timeframe: str = "1Min"
    ) -> pd.DataFrame: ...

    async def search_instruments(self, query: str, *, limit: int = 10) -> list[dict]:
        """Optional — default returns []. Override for symbol autocomplete."""
        return []
```

`StreamService` consumes only this interface. Anything a single provider
offers beyond it (Schwab quotes, options chains, account data) is reached
through a capability check, never by widening the ABC — see
`get_market_quotes_provider()` below.

---

## 2. Implemented providers

| Provider | File | Role |
|---|---|---|
| **Alpaca** | `app/providers/alpaca_provider.py` | Default `DATA_PROVIDER`; `StockDataStream` + `list_aggs` |
| **Schwab** | `app/providers/schwab_provider.py` | OAuth2 + Streamer WS + REST `/pricehistory`; quotes, options chains, account/journal |
| **Polygon** | `app/providers/polygon_provider.py` | WS "AM" 1-min aggs + `RESTClient` |
| Polygon flat-files | `app/providers/polygon_flatfiles.py` | Lake bulk loads. **Not** a `DataProvider` — operator/script-driven |
| Polygon corp-actions | `app/providers/polygon_corp_actions.py` | Whole-market splits + dividends. Not a `DataProvider` |
| EDGAR | `app/providers/edgar.py` | SEC filings reference data |

ThetaData is used out-of-band for options EOD IV history (feeds
`options_atm_iv_daily`) and is likewise not a `DataProvider`.

### Which provider does what

| Use case | Provider | Why |
|---|---|---|
| Live WebSocket stream | `STREAM_PROVIDER` (Schwab in production) | Free with brokerage; reliable |
| On-add ~48d 1-min | Schwab REST `/pricehistory` | Same auth as the WS |
| Deep daily history | Schwab REST `/pricehistory` | 20+ years |
| Symbol autocomplete | Schwab REST `/instruments` | `search_instruments` |
| Market banner quotes | Schwab `/quotes` | Only provider exposing `get_quotes` |
| Options chains | Schwab REST `/chains` | Only chain source |
| Whole-market daily bars | Polygon REST grouped-daily | One call per market day |
| Futures deep history | Polygon flat-files (mirror → parse) | Only API designed for bulk |
| Corporate actions | Polygon REST corp-actions | Comprehensive coverage |

> **Equities minute flat-files lapsed 2026-06-12** and are not being renewed.
> The minute lake is frozen at that date; daily bars now flow through REST
> grouped-daily into `equities.polygon_daily_raw`. Futures flat-file
> entitlement is a rolling ~5yr GET window (older returns 403).

---

## 3. Selection

```python
# app/config.py
DATA_PROVIDER      # default "alpaca" — the primary/fallback provider
STREAM_PROVIDER    # → settings.effective_stream_provider  (live WS)
HISTORY_PROVIDER   # → settings.effective_history_provider (REST backfill)
```

One factory resolves all three:

```python
get_provider(name)            # "alpaca" | "polygon" | "schwab"/"thinkorswim"
get_stream_provider()         # get_provider(effective_stream_provider)
get_history_provider()        # get_provider(effective_history_provider)
get_market_quotes_provider()  # capability-based: falls back to Schwab when the
                              # primary has no get_quotes (e.g. Alpaca)
```

`get_provider` raises `ValueError` on an unknown name — no silent default.
Imports are lazy inside each branch so an unconfigured provider's SDK is never
imported.

Provider selection is read **once at startup** by `StreamService.start()`.
Switching providers requires a uvicorn restart, not just an env edit.

---

## 4. Adding a provider

### 1. Implement the class

```python
# app/providers/myprovider.py
from app.providers.base import DataProvider

class MyProvider(DataProvider):
    @classmethod
    def from_settings(cls) -> "MyProvider":
        from app.config import settings
        return cls(api_key=settings.myprovider_api_key)

    def subscribe_bars(self, callback, tickers): ...
    async def historical_df(self, symbol, start, end, timeframe="1Min"): ...
```

Follow the module rules in
[`../standards/service_modules.md`](../standards/service_modules.md):
`from_settings()` for construction, no inheritance tricks, fail loud on
missing credentials.

### 2. Register in the factory

Add a branch to `get_provider()` in `app/config.py` with a lazy import.

### 3. Add env vars

Declare them on `Settings` in `app/config.py` (the single env-var source),
then document them in `.env.example` and `CONFIG.md`.

### 4. Test

The ABC is the test surface — any suite that runs against a mocked
`SchwabProvider` runs against a mocked `MyProvider`. Unit tests go in
`app/providers/tests/`; live-credential checks are `integration`-marked or
live in `scripts/check_*.py`.

### 5. Restart uvicorn

---

## 5. Schwab token refresh

The Schwab refresh token lives **~7 days**. An expired token freezes both CH
and the lake on a recent date — if you see both tiers stuck, suspect this
first.

```bash
poetry run python scripts/schwab_get_refresh_token.py
```

Browser opens → sign in → paste the returned URL. The script writes the new
token to `data/.schwab_refresh_token` and `.env`.

**Then restart uvicorn** — the running process holds the old token in memory,
so the refresh does not take effect until it reloads.

```bash
curl -s localhost:8000/api/v1/stream/status | jq
# expect: started=true, provider="schwab", streaming_count>0
```

Local auth-enabled development must use `scripts/dev/run_auth_api.sh`, not a
bare uvicorn — see [`runbook.md`](../operations/runbook.md).

---

## 6. Known provider traps

- **Schwab stream key cap.** The Streamer caps subscriptions; symbols past the
  cap silently never deliver bars. Chunking requests does **not** raise the
  cap. See [`../ISSUES.md`](../ISSUES.md).
- **Polygon keys by ticker, not company.** Reused tickers splice unrelated
  companies into one history (V = Vivendi → Visa). `ohlcv_daily` is
  dominant-segment trimmed for this reason; untrimmed data manufactures
  momentum alpha.
- **Polygon 429s on parallel flat-file pulls.** 48 workers trips rate limits;
  the futures mirror throttles deliberately.
- **Gap-detection single-symbol poison.** Pre-loading one universe symbol
  before an auto-catchup makes `latest_loaded_date` skip that day for every
  other symbol. Verify credentials with a non-universe symbol.
