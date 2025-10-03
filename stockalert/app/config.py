import os
from pydantic import BaseModel, Field
from alpaca.data.enums import DataFeed
from dotenv import load_dotenv

load_dotenv()  # Load .env file if present


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
    data_provider: str = os.getenv("DATA_PROVIDER", "alpaca")
    
    # ─────────────────────────────────────────────────────────
    # Database Settings
    # ─────────────────────────────────────────────────────────
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://admin:password@localhost:5432/stockalerts"
    )
    
    # ─────────────────────────────────────────────────────────
    # Technical Analysis Settings
    # ─────────────────────────────────────────────────────────
    pivot_k: int = int(os.getenv("PIVOT_K", "3"))
    lookback_bars: int = int(os.getenv("LOOKBACK_BARS", "60"))
    ema_period: int = int(os.getenv("EMA_PERIOD", "50"))
    use_trend_filter: bool = os.getenv("USE_TREND_FILTER", "true").lower() == "true"
    
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
    
    @property
    def alpaca_feed_enum(self) -> DataFeed:
        """Convert string feed to DataFeed enum"""
        feed_map = {
            "iex": DataFeed.IEX,
            "sip": DataFeed.SIP,
            "otc": DataFeed.OTC
        }
        return feed_map.get(self.alpaca_feed.lower(), DataFeed.IEX)
    
    def get_config_summary(self) -> dict:
        """
        Get a summary of current configuration (for debugging/logging).
        
        Excludes sensitive information like API keys.
        """
        return {
            "data_provider": self.data_provider,
            "alpaca_feed": self.alpaca_feed,
            "monitor_preload_bars": self.monitor_preload_bars,
            "monitor_preload_days": self.monitor_preload_days,
            "backfill_default_days": self.backfill_default_days,
            "fetch_safety_margin": self.fetch_safety_margin,
            "use_parquet_cache": self.use_parquet_cache,
            "lookback_bars": self.lookback_bars,
            "pivot_k": self.pivot_k,
            "use_trend_filter": self.use_trend_filter,
        }


# Global settings instance
settings = Settings()


def get_provider():
    """
    Factory function to create data provider based on configuration.
    
    Returns:
        DataProvider instance (AlpacaProvider or PolygonProvider)
    
    Raises:
        ValueError: If unsupported provider specified
    """
    if settings.data_provider == "alpaca":
        from app.providers.alpaca_provider import AlpacaProvider
        return AlpacaProvider(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            settings.alpaca_feed_enum
        )
    elif settings.data_provider == "polygon":
        from app.providers.polygon_provider import PolygonProvider
        return PolygonProvider(settings.polygon_api_key)
    else:
        raise ValueError(f"Unsupported provider: {settings.data_provider}")
