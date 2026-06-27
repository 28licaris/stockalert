# Configuration and environment variables

All settings can be overridden via a `.env` file or environment variables. **Never commit `.env`** (ensure it is in `.gitignore`). To get started, copy `.env.example` to `.env` and fill in your values: `cp .env.example .env`.

## Data provider

- **`DATA_PROVIDER`** – `alpaca` (default), `polygon`, `schwab`, or `thinkorswim`.

### Split live vs history (optional)

- **`STREAM_PROVIDER`** – If set, used only for live WebSocket bars (`get_stream_provider()`). Example: `DATA_PROVIDER=polygon` and `STREAM_PROVIDER=schwab` keeps Polygon for REST/history while Schwab supplies real-time bars (no Polygon delayed feed).
- **`HISTORY_PROVIDER`** – If set, used for historical/backfill REST paths (`get_history_provider()`). Empty means `DATA_PROVIDER` is used for both roles when `STREAM_PROVIDER` is also empty.

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
- `CLICKHOUSE_CONNECT_TIMEOUT` – connect timeout in seconds (default `10`). Startup hard-blocks on CH schema init; this makes a down/unreachable CH fail fast with a clear error instead of hanging the boot.
- `DATA_SOURCE_TAG` – optional string stored on OHLCV rows; if unset, `DATA_PROVIDER` is used when saving bars.

Start ClickHouse locally: `docker compose --profile ch up -d` (see [docker-compose.yml](docker-compose.yml)). If port `8123` is already in use, stop the other service or change `CLICKHOUSE_PORT`.

Integration tests (`tests/integration/test_database_alert.py`): set `CLICKHOUSE_TEST=1` and ensure the credentials in `.env` match your server (many installs require `CLICKHOUSE_PASSWORD`).

## Customer identity PostgreSQL

- `IDENTITY_DATABASE_URL` – SQLAlchemy PostgreSQL URL for customer accounts,
  tenants, sessions, and future billing state. It is intentionally separate
  from ClickHouse and is empty by default in `app/config.py` until the identity
  service is enabled.

Start the lightweight PostgreSQL 17 Alpine development container and apply the
code-owned schema:

```bash
docker compose --profile identity up -d postgres
IDENTITY_DATABASE_URL=postgresql+psycopg://stockalert:stockalert_dev@localhost:5432/stockalert_identity \
  poetry run alembic upgrade head
```

The `stockalert_dev` password is local-only. Production must inject an RDS URL
from the approved secret store and require TLS. Repository integration tests
require a disposable database URL in `TEST_IDENTITY_DATABASE_URL`; its database
name must end in `_test` as a destructive-test guard.

The repository provides an isolated test container on port 5433. It uses
`tmpfs`, so its data disappears with the container and can never overwrite the
persistent development database:

```bash
docker compose --profile identity-test up -d postgres-test
TEST_IDENTITY_DATABASE_URL=postgresql+psycopg://stockalert:stockalert_test@localhost:5433/stockalert_identity_test \
  poetry run pytest tests/integration/test_identity_postgres.py
```

### Cognito customer authentication

- `AUTH_ENABLED` – fail-closed feature gate; defaults to `false`.
- `COGNITO_DOMAIN` – HTTPS Cognito managed-login origin.
- `COGNITO_ISSUER_URL` – HTTPS user-pool issuer used for exact JWT validation.
- `COGNITO_CLIENT_ID` – user-pool app client identifier.
- `COGNITO_CLIENT_SECRET` – optional confidential app-client secret.
- `COGNITO_REDIRECT_URI` – registered callback URL, normally
  `http://localhost:8000/auth/callback` in local development.
- `COGNITO_LOGOUT_URI` – registered allowed sign-out URL.
- `AUTH_SESSION_HOURS` – StockAlert opaque-session lifetime; default 8.
- `AUTH_LOGIN_TRANSACTION_MINUTES` – OAuth state/nonce/PKCE lifetime; default 10.
- `AUTH_COOKIE_NAME`, `AUTH_CSRF_COOKIE_NAME` – browser cookie names.
- `AUTH_COOKIE_SECURE` – may be `false` only for local HTTP; must be `true` in
  staging and production.

Before setting `AUTH_ENABLED=true`, apply `alembic upgrade head` and configure
the exact callback/logout URLs on the Cognito app client. Passwords, MFA
secrets, and provider tokens are not stored by these routes. The callback
validates signature, issuer, audience, token use, expiry, and nonce before
creating a StockAlert session.

## Technical analysis / divergence

- `PIVOT_K`, `LOOKBACK_BARS`, `EMA_PERIOD`, `USE_TREND_FILTER`, `MIN_PRICE_CHANGE_PCT`, `MIN_INDICATOR_CHANGE_PCT`, `MIN_PIVOT_SEPARATION` – see app defaults in `app/config.py`.

## Lake freshness (nightly ingest + reconcile)

The lake stays fresh via in-process nightly jobs that **auto-catch-up**
missing weekdays, plus a daily reconcile that heals ClickHouse from the
lake. The nightly writers **default ON** for production-grade freshness;
each is additionally gated by a non-empty `STOCK_LAKE_BUCKET` + valid
provider credentials, so a dev/CI box without them is a clean no-op.

- `POLYGON_NIGHTLY_ENABLED` (default `true`), `…_RUN_HOUR_UTC` (7), `POLYGON_NIGHTLY_SYMBOLS` (`all`) → `equities.polygon_raw`.
- `SCHWAB_NIGHTLY_ENABLED` (default `true`), `…_RUN_HOUR_UTC` (22), `SCHWAB_NIGHTLY_SYMBOLS` (`active`) → `equities.schwab_universe`.
- `FUTURES_NIGHTLY_ENABLED` (default `true`), `FUTURES_POLYGON_NIGHTLY_ENABLED` (default `true`) → futures lake tables.
- `CH_RECONCILE_ENABLED` (default `true`), `…_RUN_HOUR_UTC` (23), `…_LOOKBACK_DAYS` (7) → heals `ohlcv_1m` from the lake.
- **Not in-process:** `equities.polygon_adjusted` (the split-adjusted tier) is built by a **weekly external Spark job** (EMR/CodeBuild) — it has no auto-catchup, so wire it up + alert on failure separately.
- **Verify freshness** anytime via the `get_lake_freshness` MCP tool / `LakeFreshnessReport`, which now reports the raw, **adjusted (`polygon_adjusted`)**, and futures tiers — the adjusted entry is the only signal that the external weekly Spark job has stalled.

## Alerts and monitoring

- `ALERT_WEBHOOK_URL` – Optional; if set, alerts can be POSTed to this URL (implementation in app).
- `ALERT_MIN_SEPARATION_MIN` – Minimum minutes between alerts.

## Historical / cache

- `MONITOR_PRELOAD_BARS`, `MONITOR_PRELOAD_DAYS`, `MAX_BARS_PER_REQUEST`, `FETCH_SAFETY_MARGIN`, `DATA_SUFFICIENCY_THRESHOLD`, `USE_PARQUET_CACHE`, `PARQUET_CACHE_DIR`.
- `SYMBOL_HOTLOAD_ENABLED` (default `true`), `SYMBOL_HOTLOAD_DAYS` (default `30`) — hotload-on-add fast recent tier. `false` = stream-from-now, no backfill. 30d is sized for a <5s first paint. Independent of `LAKE_WARMUP_ENABLED` (the deep 730d lake tier). See `docs/symbol_onboarding_read_design.md`.
- `SYMBOL_GAPFILL_ENABLED` (default `true`) — read-path gap-fill fallback. When a requested window isn't in CH and the lake can't cover it (cold/new symbol), fall to a provider REST fill (Schwab tip-fill → lake + CH). Non-blocking, idempotent; equities only in v1. See `docs/symbol_onboarding_read_design.md` §3.3.
