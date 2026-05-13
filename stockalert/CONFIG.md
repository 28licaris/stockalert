# Configuration and environment variables

All settings can be overridden via a `.env` file or environment variables. **Never commit `.env`** (ensure it is in `.gitignore`). To get started, copy `.env.example` to `.env` and fill in your values: `cp .env.example .env`.

## Data provider

- **`DATA_PROVIDER`** – `alpaca` (default), `polygon`, `schwab`, or `thinkorswim`.

## Alpaca (when `DATA_PROVIDER=alpaca`)

- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` – from Alpaca dashboard.
- `ALPACA_FEED` – `iex` (default), `sip`, or `otc`.

## Schwab / Think or Swim (when `DATA_PROVIDER=schwab` or `thinkorswim`)

Store these in `.env` only; never commit.

- **`SCHWAB_CLIENT_ID`** – App key (client ID) from Charles Schwab Developer Portal → Create App.
- **`SCHWAB_CLIENT_SECRET`** – App secret from the same app.
- **`SCHWAB_REFRESH_TOKEN`** – Obtained after completing the OAuth authorization-code flow once (user signs in and app receives refresh token). Can be set in `.env` or omitted and read from the token file instead.
- **`SCHWAB_REFRESH_TOKEN_FILE`** – Optional; path to file where the refresh token is read/written (default `data/.schwab_refresh_token`). The app uses the token from env first, then from this file. Running `scripts/schwab_get_refresh_token.py` writes the token here so you don’t have to edit `.env`. Refresh tokens last about 7 days; re-run the script when it expires.
- **`SCHWAB_CALLBACK_URL`** – HTTPS callback for the one-time OAuth script. Either (A) a local URL (e.g. `https://127.0.0.1:8080/oauth/callback`) — then you paste the redirect URL after sign-in — or (B) your ngrok URL + `/callback` with `ngrok http 8765` running so the script receives the redirect automatically.
- **`SCHWAB_BASE_URL`** – Optional; default `https://api.schwabapi.com`.

**`GET /trader/v1/userPreference`** supplies **Streamer (WebSocket) connection info** for live `subscribe_bars` only. **REST market data** (e.g. **`/marketdata/v1/pricehistory`** for historical OHLCV, **`/quotes`**) needs **only a valid access token**, not user preference.

The provider also uses the Trader API for OAuth, accounts/orders/transactions (read-only), and the Streamer API for real-time bars. API specs are in `api_docs/` (Account Access, Market Data, Streamer, Security). Rate limits: market data (e.g. 120 requests/min); respect limits when backfilling.

## ClickHouse

- `CLICKHOUSE_HOST` – default `localhost`.
- `CLICKHOUSE_PORT` – HTTP port, default `8123`.
- `CLICKHOUSE_USER` – default `default`.
- `CLICKHOUSE_PASSWORD` – optional; empty for local dev.
- `CLICKHOUSE_DATABASE` – default `stocks` (created on startup if missing).
- `DATA_SOURCE_TAG` – optional string stored on OHLCV rows; if unset, `DATA_PROVIDER` is used when saving bars.

Start ClickHouse locally: `docker compose --profile ch up -d` (see [docker-compose.yml](docker-compose.yml)). If port `8123` is already in use, stop the other service or change `CLICKHOUSE_PORT`.

Integration tests (`tests/test_database_alert.py`): set `CLICKHOUSE_TEST=1` and ensure the credentials in `.env` match your server (many installs require `CLICKHOUSE_PASSWORD`).

## Technical analysis / divergence

- `PIVOT_K`, `LOOKBACK_BARS`, `EMA_PERIOD`, `USE_TREND_FILTER`, `MIN_PRICE_CHANGE_PCT`, `MIN_INDICATOR_CHANGE_PCT`, `MIN_PIVOT_SEPARATION` – see app defaults in `app/config.py`.

## Alerts and monitoring

- `ALERT_WEBHOOK_URL` – Optional; if set, alerts can be POSTed to this URL (implementation in app).
- `ALERT_MIN_SEPARATION_MIN` – Minimum minutes between alerts.

## Historical / cache

- `MONITOR_PRELOAD_BARS`, `MONITOR_PRELOAD_DAYS`, `BACKFILL_DEFAULT_DAYS`, `MAX_BARS_PER_REQUEST`, `FETCH_SAFETY_MARGIN`, `DATA_SUFFICIENCY_THRESHOLD`, `USE_PARQUET_CACHE`, `PARQUET_CACHE_DIR`.
