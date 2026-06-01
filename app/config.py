import os
from pathlib import Path

from pydantic import BaseModel
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]  # stockalert/stockalert
for _candidate in (_REPO_ROOT / ".env", _REPO_ROOT / "scripts" / ".env", _REPO_ROOT.parent / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        break
else:
    load_dotenv(override=False)


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
    # Both schedules run as asyncio background tasks from main_api.py
    # startup. Gated by their *_NIGHTLY_ENABLED flag + valid credentials.
    # ─────────────────────────────────────────────────────────
    # Polygon flat-files → equities.polygon_raw (see nightly_polygon_refresh).
    # 07:00 UTC = midnight Arizona; after Polygon's daily flat file is ready.
    polygon_nightly_enabled: bool = os.getenv("POLYGON_NIGHTLY_ENABLED", "false").lower() == "true"
    polygon_nightly_run_hour_utc: int = int(os.getenv("POLYGON_NIGHTLY_RUN_HOUR_UTC", "7"))
    # Per docs/standards/data/symbol_lifecycle.md (LOCKED): Polygon is
    # the whole-market historical archive. "all" = no universe filter
    # (Polygon flat-files contain every symbol). Override to "seed"
    # only for low-storage dev environments.
    polygon_nightly_symbols: str = os.getenv("POLYGON_NIGHTLY_SYMBOLS", "all")
    polygon_nightly_kind: str = os.getenv("POLYGON_NIGHTLY_KIND", "minute").strip().lower()

    # Schwab REST pricehistory → equities.schwab_universe (see nightly_schwab_refresh).
    # 22:00 UTC = 3 PM Arizona; ~30 min after NYSE close.
    schwab_nightly_enabled: bool = os.getenv("SCHWAB_NIGHTLY_ENABLED", "false").lower() == "true"
    schwab_nightly_run_hour_utc: int = int(os.getenv("SCHWAB_NIGHTLY_RUN_HOUR_UTC", "22"))
    # Per docs/standards/data/symbol_lifecycle.md (LOCKED): Schwab is
    # the universe-bounded provider. "active" reads from stream_universe
    # (the canonical "what's our hot universe" table).
    schwab_nightly_symbols: str = os.getenv("SCHWAB_NIGHTLY_SYMBOLS", "active")

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

    # CV13: silver_ohlcv_build_* settings removed.
    # The v2 equivalent (polygon_adjustment_job) runs OUT-OF-PROCESS
    # via EMR Serverless / CodeBuild / local Spark — it's not a
    # uvicorn-scheduled job, so no enabled / run_hour / symbols
    # settings on the Settings class.
    # CV14: silver_provider_precedence + bronze_history_start removed
    # (precedence merged into the deleted silver build; bronze
    # replaced by lake_history_start above).

    # Live-lake-writer config (TA-5.7 — closes the 8-24h Schwab live →
    # bronze freshness gap). The writer reads CH ohlcv_1m every
    # cycle_minutes and upserts into bronze.{provider}_minute. See
    # app/services/ingest/live_lake_writer.py.
    live_lake_writer_enabled: bool = (
        os.getenv("LIVE_LAKE_WRITER_ENABLED", "true").lower() == "true"
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
    
    # Backfill/research settings (more historical data)
    backfill_default_days: int = int(os.getenv("BACKFILL_DEFAULT_DAYS", "30"))
    
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
            "backfill_default_days": self.backfill_default_days,
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