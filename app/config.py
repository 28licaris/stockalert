import os
from pathlib import Path

from pydantic import BaseModel
from dotenv import dotenv_values, load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]  # stockalert/stockalert
_ENV_FILE: Path | None = None
for _candidate in (_REPO_ROOT / ".env", _REPO_ROOT / "scripts" / ".env", _REPO_ROOT.parent / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        _ENV_FILE = _candidate
        break
else:
    load_dotenv(override=False)


def _normalize_aws_env() -> None:
    """Make AWS credential resolution robust against a polluted ambient env.

    boto3/PyIceberg read AWS creds from ``os.environ`` at client-build time.
    Two failure modes bite the lake-backed readers:

      1. A blank ``AWS_PROFILE`` exported by the launching shell. Because
         ``load_dotenv(override=False)`` will not replace an already-present
         (even if empty) value, the profile our .env intends never lands, and
         boto3 falls through to the EC2 metadata service — which hangs ~45-90s
         per call on a non-EC2 host. This silently breaks every lake endpoint
         and the CH→S3 chart fallback.

      2. Empty explicit-key vars (``AWS_ACCESS_KEY_ID=``) left in .env, which
         can trip botocore's partial-credential detection.

    Fix: drop blank explicit-key vars, and force the profile our .env declares
    whenever the ambient ``AWS_PROFILE`` is blank. Idempotent; safe to call at
    import time before any boto3 client is constructed.
    """
    for _k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        if os.environ.get(_k, "").strip() == "":
            os.environ.pop(_k, None)

    if not os.environ.get("AWS_PROFILE", "").strip():
        _file_vals = dotenv_values(_ENV_FILE) if _ENV_FILE else {}
        _want = (_file_vals.get("AWS_PROFILE") or "").strip()
        if _want:
            os.environ["AWS_PROFILE"] = _want


_normalize_aws_env()


class Settings(BaseModel):
    """
    Application settings with environment variable support.
    
    All settings can be overridden via .env file or environment variables.
    """
    
    # ─────────────────────────────────────────────────────────
    # Data Provider Settings
    # ─────────────────────────────────────────────────────────
    alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
    alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_feed: str = os.getenv("ALPACA_FEED", "iex")
    polygon_api_key: str = os.getenv("POLYGON_API_KEY", "")
    polygon_feed: str = os.getenv("POLYGON_FEED", "socket.polygon.io")
    polygon_market: str = os.getenv("POLYGON_MARKET", "stocks")
    polygon_secure_ws: bool = os.getenv("POLYGON_SECURE_WS", "true").lower() == "true"
    polygon_flatfiles_enabled: bool = os.getenv("POLYGON_FLATFILES_ENABLED", "false").lower() == "true"
    polygon_s3_access_key_id: str = os.getenv("POLYGON_S3_ACCESS_KEY_ID", "")
    polygon_s3_secret_access_key: str = os.getenv("POLYGON_S3_SECRET_ACCESS_KEY", "")
    polygon_s3_endpoint: str = os.getenv("POLYGON_S3_ENDPOINT", "https://files.massive.com")
    polygon_s3_bucket: str = os.getenv("POLYGON_S3_BUCKET", "flatfiles")
    data_provider: str = os.getenv("DATA_PROVIDER", "alpaca")
    # Optional role-specific overrides. Empty values fall back to DATA_PROVIDER.
    stream_provider: str = os.getenv("STREAM_PROVIDER", "")
    history_provider: str = os.getenv("HISTORY_PROVIDER", "")

    # ─────────────────────────────────────────────────────────
    # Stock Lake (S3) — your own data lake, separate from Polygon Flat Files.
    # See docs/architecture_v2/03_s3_layout.md for layout. Empty AWS creds fall through
    # to the default boto3 credential chain (env, ~/.aws/credentials, IAM
    # role, etc.), so deploys on EC2 / ECS need only set the bucket name.
    # ─────────────────────────────────────────────────────────
    stock_lake_bucket: str = os.getenv("STOCK_LAKE_BUCKET", "")
    stock_lake_region: str = os.getenv("STOCK_LAKE_REGION", "us-east-1")
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    aws_session_token: str = os.getenv("AWS_SESSION_TOKEN", "")
    # ─────────────────────────────────────────────────────────
    # Nightly per-provider ingest jobs (CV7/CV8 — v2 lake writers).
    # Run as asyncio background tasks from main_api.py startup.
    # DEFAULT ON (production-grade freshness): the lake self-heals nightly
    # (each job auto-catches-up missing weekdays). Each is additionally
    # gated by a non-empty STOCK_LAKE_BUCKET + valid provider credentials,
    # so a dev/CI box without creds/bucket is a clean no-op (logs the
    # gate reason, never runs). Set the *_NIGHTLY_ENABLED env var to
    # "false" to opt a specific writer out.
    # ─────────────────────────────────────────────────────────
    # Polygon flat-files → equities.polygon_raw (see nightly_equities_polygon_refresh).
    # 07:00 UTC = midnight Arizona; after Polygon's daily flat file is ready.
    polygon_nightly_enabled: bool = os.getenv("POLYGON_NIGHTLY_ENABLED", "true").lower() == "true"
    polygon_nightly_run_hour_utc: int = int(os.getenv("POLYGON_NIGHTLY_RUN_HOUR_UTC", "7"))
    # Per docs/standards/data/symbol_lifecycle.md (LOCKED): Polygon is
    # the whole-market historical archive. "all" = no universe filter
    # (Polygon flat-files contain every symbol). Override to "active"
    # or an explicit CSV only for deliberately bounded development runs.
    polygon_nightly_symbols: str = os.getenv("POLYGON_NIGHTLY_SYMBOLS", "all")
    polygon_nightly_kind: str = os.getenv("POLYGON_NIGHTLY_KIND", "minute").strip().lower()

    # Nightly Elliott Wave recompute (EW-3). OFF by default — enabling it wires
    # a daily background loop that labels `elliott_recompute_symbols` and appends
    # to `<ns>.elliott_wave_labels`. Run hour is UTC; default 22:00 (after the
    # equities session settles). Symbols is a comma-separated list (`/`-prefixed
    # = futures); empty means the loop no-ops. See docs/elliott_wave_system_spec.md §4.
    elliott_recompute_enabled: bool = os.getenv("ELLIOTT_RECOMPUTE_ENABLED", "false").lower() == "true"
    elliott_recompute_run_hour_utc: int = int(os.getenv("ELLIOTT_RECOMPUTE_RUN_HOUR_UTC", "22"))
    elliott_recompute_symbols: str = os.getenv("ELLIOTT_RECOMPUTE_SYMBOLS", "")
    elliott_recompute_intervals: str = os.getenv("ELLIOTT_RECOMPUTE_INTERVALS", "1d")

    # Live intraday Elliott Wave scanner (EW-7 live path).  Fires wave alerts
    # on each incoming bar by re-running compute_labeling(source=AUTO) and
    # broadcasting via the WebSocket signal multiplex.  OFF by default.
    # ELLIOTT_LIVE_SCANNER_SYMBOLS: comma-separated list, e.g. "AAPL,TSLA,/ES"
    # ELLIOTT_LIVE_SCANNER_INTERVAL: bar interval to scan (default "5m")
    elliott_live_scanner_enabled: bool = (
        os.getenv("ELLIOTT_LIVE_SCANNER_ENABLED", "false").lower() == "true"
    )
    elliott_live_scanner_symbols: str = os.getenv("ELLIOTT_LIVE_SCANNER_SYMBOLS", "")
    elliott_live_scanner_interval: str = os.getenv("ELLIOTT_LIVE_SCANNER_INTERVAL", "5m")

    # Schwab REST pricehistory → equities.schwab_universe (see nightly_schwab_refresh).
    # 22:00 UTC = 3 PM Arizona; ~30 min after NYSE close.
    schwab_nightly_enabled: bool = os.getenv("SCHWAB_NIGHTLY_ENABLED", "true").lower() == "true"
    schwab_nightly_run_hour_utc: int = int(os.getenv("SCHWAB_NIGHTLY_RUN_HOUR_UTC", "22"))
    # Per docs/standards/data/symbol_lifecycle.md (LOCKED): Schwab is
    # the universe-bounded provider. "active" reads from stream_universe
    # (the canonical "what's our hot universe" table).
    schwab_nightly_symbols: str = os.getenv("SCHWAB_NIGHTLY_SYMBOLS", "active")

    # Schwab REST pricehistory → futures.schwab_futures (see
    # nightly_futures_refresh). Same shape as the equities nightly, gated
    # separately so futures can be toggled independently. "active" reads
    # the continuous roots from stocks.futures_universe (∪ seed fallback);
    # "seed" forces the static FUTURES_SEED_ROOTS list.
    futures_nightly_enabled: bool = os.getenv("FUTURES_NIGHTLY_ENABLED", "true").lower() == "true"
    futures_nightly_run_hour_utc: int = int(os.getenv("FUTURES_NIGHTLY_RUN_HOUR_UTC", "22"))
    futures_nightly_symbols: str = os.getenv("FUTURES_NIGHTLY_SYMBOLS", "active")

    # Polygon flat-file → futures.polygon_raw → futures.polygon_continuous
    # (see nightly_futures_polygon_refresh). Keeps the authoritative back-
    # adjusted deep history fresh; complements the Schwab nightly (recent tip).
    # Gated separately (heavy per-root rebuild). Default run hour 21 UTC —
    # Polygon finalizes a day's files ~11:00 ET next morning.
    futures_polygon_nightly_enabled: bool = os.getenv("FUTURES_POLYGON_NIGHTLY_ENABLED", "true").lower() == "true"
    futures_polygon_nightly_run_hour_utc: int = int(os.getenv("FUTURES_POLYGON_NIGHTLY_RUN_HOUR_UTC", "21"))

    # CH reconcile — push the authoritative, complete lake tables
    # (equities.schwab_universe → ohlcv_1m, futures.schwab_futures →
    # futures_ohlcv_1m) back into ClickHouse so live-stream gaps
    # (restarts/outages) self-heal. Runs daily AFTER the nightly refreshes
    # (default 23:00 UTC vs their 22:00). See ch_reconcile.py.
    ch_reconcile_enabled: bool = os.getenv("CH_RECONCILE_ENABLED", "true").lower() == "true"
    ch_reconcile_run_hour_utc: int = int(os.getenv("CH_RECONCILE_RUN_HOUR_UTC", "23"))
    ch_reconcile_lookback_days: int = int(os.getenv("CH_RECONCILE_LOOKBACK_DAYS", "7"))

    # ─────────────────────────────────────────────────────────
    # Iceberg catalog (AWS Glue) — see docs/architecture_v2/.
    # Iceberg warehouse path: s3://${STOCK_LAKE_BUCKET}/${ICEBERG_WAREHOUSE_PREFIX}/
    # Glue database holding namespaces bronze/silver/gold (we use one Glue
    # database; Iceberg namespaces are the `bronze.*` etc. qualifiers).
    # ─────────────────────────────────────────────────────────
    iceberg_catalog_name: str = os.getenv("ICEBERG_CATALOG_NAME", "stock_lake")
    iceberg_glue_database: str = os.getenv("ICEBERG_GLUE_DATABASE", "stock_lake")
    iceberg_warehouse_prefix: str = os.getenv("ICEBERG_WAREHOUSE_PREFIX", "iceberg").strip("/")

    # Architecture v2 — separate Glue database for the equities namespace.
    # Tables: polygon_raw, polygon_adjusted, schwab_universe,
    # market_corp_actions. Fully-qualified as `lake.equities.<table>` when
    # read via Spark/DuckDB; via PyIceberg as `<this_db>.<table>`.
    # Created by `app/services/equities/tables.py::ensure_*`.
    # Spec: docs/architecture_v2/ (Gate 1 — equities database name).
    iceberg_equities_glue_database: str = os.getenv(
        "ICEBERG_EQUITIES_GLUE_DATABASE", "equities"
    )
    # Futures live in their own Glue DB + S3 folder (iceberg/futures/),
    # separate from equities. No adjustment tier (futures have no splits).
    iceberg_futures_glue_database: str = os.getenv(
        "ICEBERG_FUTURES_GLUE_DATABASE", "futures"
    )

    # Earliest date for which we have OHLCV coverage. Operator scripts
    # (Athena bulk-import, history backfill) use this as the lower
    # bound. Override when you extend Polygon coverage further back
    # (e.g. 20-year subscription upgrade: LAKE_HISTORY_START=2006-01-04).
    # Env var name retained for backwards compat with operator .env
    # files that haven't been migrated.
    lake_history_start: str = os.getenv(
        "LAKE_HISTORY_START",
        os.getenv("BRONZE_HISTORY_START", "2021-01-04"),
    )

    # Lake-warmup flow (was silver-derived add_members; CV15 rename).
    # When True, stream_service.add fires the lake-warmup chain on
    # newly-added symbols (CV12):
    #   1. tip_fill (Schwab REST → equities.schwab_universe + CH; 48d)
    #   2. lake_to_ch_backfill (equities.polygon_adjusted → CH; 730d)
    # Both run in parallel — new-symbol latency target <10s end-to-end.
    # When False (default), uses the legacy _enqueue_backfill 3-call
    # path (provider REST → CH direct = Path ②). Flip to True once
    # CV4 (Athena bulk-import) + CV6 (Spark adjustment) have run in
    # production. Env var name retained for backwards compat with
    # operator .env files; semantics replaced.
    lake_warmup_enabled: bool = (
        os.getenv(
            "LAKE_WARMUP_ENABLED",
            os.getenv("SILVER_DERIVED_ADD_MEMBERS_ENABLED", "false"),
        ).lower() == "true"
    )

    # Hotload-on-add (the fast recent tier). When True, adding a symbol
    # seeds the last `symbol_hotload_days` of 1m bars into the CH hot
    # cache via the latency-first quick backfill (provider-agnostic;
    # uses the configured history provider, Schwab by default) so the
    # chart paints quickly without waiting on the weekly lake.
    # When False: pure "stream from now" — subscribe live, no backfill.
    # Independent of `lake_warmup_enabled` (the deep 730d lake tier).
    # Spec: docs/symbol_onboarding_read_design.md §3.1.
    # `symbol_hotload_days` default 30 is sized for a <5s first paint
    # (quick backfill chunks Schwab's ~10d/call cap); tune down (e.g. 7)
    # if a provider/window pushes past the budget.
    symbol_hotload_enabled: bool = (
        os.getenv("SYMBOL_HOTLOAD_ENABLED", "true").lower() == "true"
    )
    symbol_hotload_days: int = int(os.getenv("SYMBOL_HOTLOAD_DAYS", "30"))

    # Read-path gap-fill (edge case). When the bars gateway's lake→CH
    # fill comes up empty for a requested window — i.e. the lake (ground
    # truth) has nothing, typically a brand-new/cold symbol — fall to a
    # provider REST fill (Schwab tip-fill → schwab_universe lake + CH) so
    # the chart self-heals on demand. Non-blocking, idempotent,
    # single-flight. The nightly provider jobs own the steady state; this
    # is the on-demand fallback only. Equities only in v1 (futures + a
    # pluggable provider knob are follow-ups). Spec:
    # docs/symbol_onboarding_read_design.md §3.3.
    symbol_gapfill_enabled: bool = (
        os.getenv("SYMBOL_GAPFILL_ENABLED", "true").lower() == "true"
    )

    # CV13: silver_ohlcv_build_* settings removed.
    # The v2 equivalent (polygon_adjustment_job) runs OUT-OF-PROCESS
    # via EMR Serverless / CodeBuild / local Spark — it's not a
    # uvicorn-scheduled job, so no enabled / run_hour / symbols
    # settings on the Settings class.
    # CV14: silver_provider_precedence + bronze_history_start removed
    # (precedence merged into the deleted silver build; bronze
    # replaced by lake_history_start above).

    # Live-lake-writer config. DISABLED by default as of the live→CH
    # architecture decision: Schwab live data flows into ClickHouse only
    # (the hot tier); the S3 lake is written infrequently — nightly for
    # equities.schwab_universe (nightly_schwab_refresh) and weekly for
    # equities.polygon_adjusted (Spark). This writer's 5-min upserts to
    # schwab_universe were redundant (every row already lands in CH from
    # the stream) and were the source of severe small-file fragmentation
    # (~10k tiny files). Leave OFF unless you specifically need an
    # intraday durable S3 copy of live bars; the nightly REST refresh
    # re-pulls each day authoritatively. See
    # app/services/ingest/live_lake_writer.py.
    live_lake_writer_enabled: bool = (
        os.getenv("LIVE_LAKE_WRITER_ENABLED", "false").lower() == "true"
    )
    live_lake_writer_cycle_minutes: int = int(
        os.getenv("LIVE_LAKE_WRITER_CYCLE_MINUTES", "5")
    )
    live_lake_writer_lookback_minutes: int = int(
        os.getenv("LIVE_LAKE_WRITER_LOOKBACK_MINUTES", "15")
    )

    # Schwab (Think or Swim) – store credentials in .env only; never commit
    schwab_client_id: str = os.getenv("SCHWAB_CLIENT_ID", "")
    schwab_client_secret: str = os.getenv("SCHWAB_CLIENT_SECRET", "")
    schwab_refresh_token: str = os.getenv("SCHWAB_REFRESH_TOKEN", "")
    schwab_refresh_token_file: str = os.getenv("SCHWAB_REFRESH_TOKEN_FILE", "data/.schwab_refresh_token")
    schwab_callback_url: str = os.getenv("SCHWAB_CALLBACK_URL", "")
    # Empty SCHWAB_BASE_URL in .env would otherwise become "" and break API URLs (relative path → DNS error).
    schwab_base_url: str = (
        os.getenv("SCHWAB_BASE_URL", "https://api.schwabapi.com").strip() or "https://api.schwabapi.com"
    )
    journal_enabled: bool = os.getenv("JOURNAL_ENABLED", "true").lower() == "true"
    
    # ─────────────────────────────────────────────────────────
    # ClickHouse (time-series)
    # ─────────────────────────────────────────────────────────
    clickhouse_host: str = os.getenv("CLICKHOUSE_HOST", "localhost")
    clickhouse_port: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    clickhouse_user: str = os.getenv("CLICKHOUSE_USER", "default")
    clickhouse_password: str = os.getenv("CLICKHOUSE_PASSWORD", "")
    clickhouse_database: str = os.getenv("CLICKHOUSE_DATABASE", "stocks")
    # Connect timeout (seconds) so a down/unreachable ClickHouse fails the
    # (blocking) startup schema-init FAST with a clear error instead of
    # hanging the boot. Query timeout is separate (send_receive_timeout).
    clickhouse_connect_timeout: int = int(os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "10"))

    # Customer identity / subscription operational database. This remains
    # separate from ClickHouse by design: PostgreSQL owns accounts, tenants,
    # sessions, and billing state; ClickHouse owns market/alert analytics.
    # Empty by default so existing developer-cockpit deployments do not
    # accidentally connect to a relational database before auth is enabled.
    identity_database_url: str = os.getenv("IDENTITY_DATABASE_URL", "")
    auth_enabled: bool = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    cognito_domain: str = os.getenv("COGNITO_DOMAIN", "").rstrip("/")
    cognito_issuer_url: str = os.getenv("COGNITO_ISSUER_URL", "").rstrip("/")
    cognito_client_id: str = os.getenv("COGNITO_CLIENT_ID", "")
    cognito_client_secret: str = os.getenv("COGNITO_CLIENT_SECRET", "")
    cognito_redirect_uri: str = os.getenv(
        "COGNITO_REDIRECT_URI", "http://localhost:8000/auth/callback"
    )
    cognito_logout_uri: str = os.getenv(
        "COGNITO_LOGOUT_URI", "http://localhost:8000/app/login"
    )
    auth_session_hours: int = int(os.getenv("AUTH_SESSION_HOURS", "8"))
    auth_login_transaction_minutes: int = int(
        os.getenv("AUTH_LOGIN_TRANSACTION_MINUTES", "10")
    )
    auth_cookie_name: str = os.getenv("AUTH_COOKIE_NAME", "stockalert_session")
    auth_csrf_cookie_name: str = os.getenv(
        "AUTH_CSRF_COOKIE_NAME", "stockalert_csrf"
    )
    auth_cookie_secure: bool = (
        os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
    )
    auth_provider_token_cipher: str = os.getenv(
        "AUTH_PROVIDER_TOKEN_CIPHER", "disabled"
    ).lower()
    auth_provider_token_kms_key_id: str = os.getenv(
        "AUTH_PROVIDER_TOKEN_KMS_KEY_ID", ""
    )
    auth_provider_token_kms_region: str = os.getenv(
        "AUTH_PROVIDER_TOKEN_KMS_REGION", "us-east-1"
    )
    # Stripe billing (customer subscriptions). Empty by default → the billing
    # API degrades to a clean 503 "billing_not_configured" rather than failing
    # at import/startup. Requires the identity PostgreSQL DB (subscription
    # state lives there). See app/services/billing/.
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_price_monthly: str = os.getenv("STRIPE_PRICE_MONTHLY", "")
    stripe_price_annual: str = os.getenv("STRIPE_PRICE_ANNUAL", "")
    billing_trial_days: int = int(os.getenv("BILLING_TRIAL_DAYS", "0") or 0)
    billing_return_url: str = os.getenv(
        "BILLING_RETURN_URL", "http://localhost:8000/app/settings"
    )
    # SEC EDGAR news/filings feed. EDGAR requires a User-Agent with a real
    # contact email (≤10 req/s). Free, no key. See app/providers/edgar.py.
    edgar_user_agent: str = os.getenv(
        "EDGAR_USER_AGENT", "StockAlert/1.0 (contact: set-EDGAR_USER_AGENT@example.com)"
    )

    # Optional tag stored on OHLCV rows (e.g. matches DATA_PROVIDER)
    data_source_tag: str = os.getenv("DATA_SOURCE_TAG", "")
    # Comma-separated symbols for the dashboard tape. Uses liquid ETFs that
    # Schwab quotes reliably (no index/futures syntax). Override via
    # MARKET_BANNER_SYMBOLS in .env.
    market_banner_symbols: str = os.getenv(
        "MARKET_BANNER_SYMBOLS",
        "SPY,QQQ,IWM,DIA,GLD,TLT,VIXY,SLV",
    )
    
    # ─────────────────────────────────────────────────────────
    # Technical Analysis Settings (RELAXED for 1-min bars)
    # ─────────────────────────────────────────────────────────
    pivot_k: int = int(os.getenv("PIVOT_K", "4"))  # Changed from 3 to 4
    lookback_bars: int = int(os.getenv("LOOKBACK_BARS", "80"))  # Increased from 60
    ema_period: int = int(os.getenv("EMA_PERIOD", "50"))
    use_trend_filter: bool = os.getenv("USE_TREND_FILTER", "false").lower() == "true"  # DISABLED by default
    
    # Quality thresholds (relaxed for 1-minute data)
    min_price_change_pct: float = float(os.getenv("MIN_PRICE_CHANGE_PCT", "0.003"))  # 0.3%
    min_indicator_change_pct: float = float(os.getenv("MIN_INDICATOR_CHANGE_PCT", "0.02"))  # 2%
    min_pivot_separation: int = int(os.getenv("MIN_PIVOT_SEPARATION", "12"))  # 12 minutes
    
    # ─────────────────────────────────────────────────────────
    # Historical Data Loading Settings
    # ─────────────────────────────────────────────────────────
    # Monitor preload settings (fast startup)
    monitor_preload_bars: int = int(os.getenv("MONITOR_PRELOAD_BARS", "200"))
    monitor_preload_days: int = int(os.getenv("MONITOR_PRELOAD_DAYS", "5"))

    # API fetch optimization
    max_bars_per_request: int = int(os.getenv("MAX_BARS_PER_REQUEST", "10000"))
    fetch_safety_margin: float = float(os.getenv("FETCH_SAFETY_MARGIN", "1.3"))
    
    # Data sufficiency threshold (0.8 = need 80% of requested bars minimum)
    data_sufficiency_threshold: float = float(
        os.getenv("DATA_SUFFICIENCY_THRESHOLD", "0.8")
    )
    
    # ─────────────────────────────────────────────────────────
    # Cache Settings
    # ─────────────────────────────────────────────────────────
    use_parquet_cache: bool = os.getenv("USE_PARQUET_CACHE", "false").lower() == "true"
    parquet_cache_dir: str = os.getenv("PARQUET_CACHE_DIR", "./data/parquet")
    
    # ─────────────────────────────────────────────────────────
    # Alert Settings
    # ─────────────────────────────────────────────────────────
    alert_webhook_url: str | None = os.getenv("ALERT_WEBHOOK_URL")
    alert_min_separation_min: int = int(os.getenv("ALERT_MIN_SEPARATION_MIN", "15"))
    
    # ─────────────────────────────────────────────────────────
    # Monitor Health Check Settings
    # ─────────────────────────────────────────────────────────
    heartbeat_interval_seconds: int = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "600"))
    max_idle_time_seconds: int = int(os.getenv("MAX_IDLE_TIME_SECONDS", "3600"))

    def get_schwab_refresh_token(self) -> str:
        """
        Effective Schwab refresh token: from SCHWAB_REFRESH_TOKEN env, or from
        token file (SCHWAB_REFRESH_TOKEN_FILE) so you don't have to edit .env
        after running the one-time OAuth script.
        """
        if self.schwab_refresh_token:
            return self.schwab_refresh_token.strip()
        path = self.schwab_refresh_token_file
        if not path:
            return ""
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    return f.read().strip()
            except OSError:
                pass
        return ""

    @property
    def alpaca_feed_enum(self):
        """Convert string feed to DataFeed enum. Lazy-imports Alpaca so Schwab-only deploys don't require alpaca-py at import."""
        from alpaca.data.enums import DataFeed
        feed_map = {"iex": DataFeed.IEX, "sip": DataFeed.SIP, "otc": DataFeed.OTC}
        return feed_map.get(self.alpaca_feed.lower(), DataFeed.IEX)

    def get_config_summary(self) -> dict:
        """
        Get a summary of current configuration (for debugging/logging).
        
        Excludes sensitive information like API keys.
        """
        return {
            "data_provider": self.data_provider,
            "stream_provider": self.effective_stream_provider,
            "history_provider": self.effective_history_provider,
            "alpaca_feed": self.alpaca_feed,
            "polygon_feed": self.polygon_feed,
            "polygon_market": self.polygon_market,
            "polygon_flatfiles_enabled": self.polygon_flatfiles_enabled,
            "journal_enabled": self.journal_enabled,
            "monitor_preload_bars": self.monitor_preload_bars,
            "monitor_preload_days": self.monitor_preload_days,
            "fetch_safety_margin": self.fetch_safety_margin,
            "use_parquet_cache": self.use_parquet_cache,
            "lookback_bars": self.lookback_bars,
            "pivot_k": self.pivot_k,
            "use_trend_filter": self.use_trend_filter,
            "min_price_change_pct": self.min_price_change_pct,
            "min_indicator_change_pct": self.min_indicator_change_pct,
            "min_pivot_separation": self.min_pivot_separation,
        }

    @property
    def effective_stream_provider(self) -> str:
        return (self.stream_provider or self.data_provider).strip().lower()

    @property
    def effective_history_provider(self) -> str:
        return (self.history_provider or self.data_provider).strip().lower()


# Global settings instance
settings = Settings()


def get_provider(provider_name: str | None = None):
    """
    Factory function to create data provider based on configuration.
    
    Returns:
        DataProvider instance (AlpacaProvider or PolygonProvider)
    
    Raises:
        ValueError: If unsupported provider specified
    """
    provider = (provider_name or settings.data_provider).strip().lower()
    if provider == "alpaca":
        from app.providers.alpaca_provider import AlpacaProvider
        return AlpacaProvider(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            settings.alpaca_feed_enum,
        )
    elif provider == "polygon":
        from app.providers.polygon_provider import PolygonProvider
        return PolygonProvider(
            settings.polygon_api_key,
            feed=settings.polygon_feed,
            market=settings.polygon_market,
            secure_ws=settings.polygon_secure_ws,
        )
    elif provider in ("schwab", "thinkorswim"):
        from app.providers.schwab_provider import SchwabProvider
        return SchwabProvider(
            client_id=settings.schwab_client_id,
            client_secret=settings.schwab_client_secret,
            refresh_token=settings.get_schwab_refresh_token(),
            callback_url=settings.schwab_callback_url or None,
            base_url=settings.schwab_base_url,
            refresh_token_file=settings.schwab_refresh_token_file or None,
        )
    else:
        raise ValueError(f"Unsupported provider: {provider}")


def get_stream_provider():
    """Provider used for live WebSocket bars."""
    return get_provider(settings.effective_stream_provider)


def get_history_provider():
    """Provider used for REST historical bars and backfill jobs."""
    return get_provider(settings.effective_history_provider)


def get_market_quotes_provider():
    """
    Provider for GET /api/market/banner (Schwab Market Data ``/quotes``).

    Uses the configured ``DATA_PROVIDER`` when it exposes ``get_quotes`` (Schwab).
    If the primary provider has no quotes (e.g. Alpaca) but Schwab OAuth is
    configured, returns a ``SchwabProvider`` so the tape still works.
    """
    p = get_provider()
    if getattr(p, "get_quotes", None) is not None:
        return p
    cid = (settings.schwab_client_id or "").strip()
    csec = (settings.schwab_client_secret or "").strip()
    if cid and csec and settings.get_schwab_refresh_token():
        from app.providers.schwab_provider import SchwabProvider

        return SchwabProvider(
            client_id=settings.schwab_client_id,
            client_secret=settings.schwab_client_secret,
            refresh_token=settings.get_schwab_refresh_token(),
            callback_url=settings.schwab_callback_url or None,
            base_url=settings.schwab_base_url,
            refresh_token_file=settings.schwab_refresh_token_file or None,
        )
    return p
