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
    # See `storage_plan.md` for layout. Empty AWS creds fall through to the
    # default boto3 credential chain (env, ~/.aws/credentials, IAM role, etc.),
    # so deploys on EC2 / ECS need only set the bucket name.
    # ─────────────────────────────────────────────────────────
    stock_lake_bucket: str = os.getenv("STOCK_LAKE_BUCKET", "")
    stock_lake_region: str = os.getenv("STOCK_LAKE_REGION", "us-east-1")
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    aws_session_token: str = os.getenv("AWS_SESSION_TOKEN", "")
    # Daily archive worker toggle. Even when set true, the worker no-ops if
    # `stock_lake_bucket` is empty so misconfigured deploys don't crash.
    lake_archive_enabled: bool = os.getenv("LAKE_ARCHIVE_ENABLED", "false").lower() == "true"
    # UTC hour to run the daily archive sweep at. 07:00 UTC == 03:00 ET,
    # after extended-hours close so we operate on a complete prior trading day.
    lake_archive_run_hour_utc: int = int(os.getenv("LAKE_ARCHIVE_RUN_HOUR_UTC", "7"))
    # Nightly in-process Polygon flat-files → S3 lake (see nightly_lake_refresh).
    nightly_lake_symbols: str = os.getenv("NIGHTLY_LAKE_SYMBOLS", "seed")
    nightly_lake_kind: str = os.getenv("NIGHTLY_LAKE_KIND", "minute").strip().lower()

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
    # Comma-separated symbols for the dashboard tape (indexes, ETFs, explicit
    # futures roots like /ESM26). Override via MARKET_BANNER_SYMBOLS in .env.
    market_banner_symbols: str = os.getenv(
        "MARKET_BANNER_SYMBOLS",
        "$SPX,$NDX,$DJI,$RUT,$VIX,/ESM26,/MNQM26,/CLM26,/GCM26",
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