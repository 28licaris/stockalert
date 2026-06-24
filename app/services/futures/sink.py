"""Iceberg sink for ``futures.schwab_futures`` (1-min OHLCV, no adjustment).

Thin factory over the generic OHLCV minute-bar writer
(``EquitiesIcebergSink``): futures share the canonical 12-column shape but
have NO ``adj_factor`` (futures have no splits/dividends), so we pass
``static_adj_factor=None`` with a futures arrow schema. See
``app/services/futures/schemas.py`` for the table contract.
"""
from __future__ import annotations

import pyarrow as pa

from app.services.equities.sink import EquitiesIcebergSink
from app.services.futures.tables import (
    FUTURES_DAILY_TABLE_NAME,
    FUTURES_TABLE_NAME,
    ensure_schwab_futures,
    ensure_schwab_futures_daily,
)
from app.services.iceberg_catalog import get_catalog

# 12 canonical OHLCV columns — ``futures.schwab_futures`` shape. Same byte
# layout as equities ``_POLYGON_RAW_ARROW`` (no adj_factor); declared here
# so the futures package doesn't reach into equities-sink internals.
_SCHWAB_FUTURES_ARROW = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=True),
        pa.field("high", pa.float64(), nullable=True),
        pa.field("low", pa.float64(), nullable=True),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("volume", pa.float64(), nullable=True),
        pa.field("vwap", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("source", pa.string(), nullable=True),
        pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("ingestion_run_id", pa.string(), nullable=True),
    ]
)

# Schwab REST + WebSocket are the only futures writers; both tag rows
# ``schwab`` / ``minute``. (The live S3 archive path, when added, reuses
# this same sink.)
_FUTURES_ACCEPTED_PROVIDERS = {
    ("schwab", "minute"),
    ("schwab-rest", "minute"),
    ("schwab-live", "minute"),
}


def futures_iceberg_sink() -> EquitiesIcebergSink:
    """Construct the ``futures.schwab_futures`` sink (creates the table if
    absent). Returns the generic OHLCV writer wired for the futures schema —
    no adjustment column."""
    catalog = get_catalog()
    table = ensure_schwab_futures(catalog)
    return EquitiesIcebergSink(
        table=table,
        name=f"futures_{FUTURES_TABLE_NAME}",
        arrow_schema=_SCHWAB_FUTURES_ARROW,
        accepted_providers=_FUTURES_ACCEPTED_PROVIDERS,
        static_adj_factor=None,
    )


def futures_daily_iceberg_sink() -> EquitiesIcebergSink:
    """Construct the ``futures.schwab_futures_daily`` sink (creates the table if
    absent). Same column shape as the 1-minute sink; `accepted_providers=None`
    so the daily backfill (tagged kind='day') is written without a provider
    allowlist check."""
    table = ensure_schwab_futures_daily(get_catalog())
    return EquitiesIcebergSink(
        table=table,
        name=f"futures_{FUTURES_DAILY_TABLE_NAME}",
        arrow_schema=_SCHWAB_FUTURES_ARROW,
        accepted_providers=None,
        static_adj_factor=None,
    )
