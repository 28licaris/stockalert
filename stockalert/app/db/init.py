"""Idempotent ClickHouse DDL (safe to run on every startup)."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.config import settings
from app.db.client import get_admin_client, get_client

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_NAME = "default"
LEGACY_WATCHLIST_JSON = "data/watchlist.json"


def init_schema() -> None:
    db = settings.clickhouse_database
    admin = get_admin_client()
    admin.command(f"CREATE DATABASE IF NOT EXISTS `{db}`")
    admin.close()

    client = get_client()

    client.command(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_1m (
            symbol        LowCardinality(String),
            timestamp     DateTime64(3, 'UTC'),
            open          Float64,
            high          Float64,
            low           Float64,
            close         Float64,
            volume        Float64,
            vwap          Float64 DEFAULT 0,
            trade_count   UInt32 DEFAULT 0,
            source        LowCardinality(String) DEFAULT '',
            version       UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(timestamp)
        ORDER BY (symbol, timestamp)
        SETTINGS index_granularity = 8192
        """
    )

    # Medium-resolution intraday bars. Schwab's pricehistory serves 5-minute
    # candles ~270 days back per call; we use this table as the source for
    # 5m / 15m / 30m / 1h / 4h queries that need MORE than 48 days of history
    # (the 1-min limit). Same shape as `ohlcv_1m` so resampling logic can be
    # parameterized on the source table name.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_5m (
            symbol        LowCardinality(String),
            timestamp     DateTime64(3, 'UTC'),
            open          Float64,
            high          Float64,
            low           Float64,
            close         Float64,
            volume        Float64,
            vwap          Float64 DEFAULT 0,
            trade_count   UInt32 DEFAULT 0,
            source        LowCardinality(String) DEFAULT '',
            version       UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(timestamp)
        ORDER BY (symbol, timestamp)
        SETTINGS index_granularity = 8192
        """
    )

    # Daily bars are stored in their own table because Schwab's pricehistory
    # endpoint can serve daily candles 20+ years back, while 1-min bars are
    # capped at ~48 days. Keeping daily separate also avoids confusing the
    # 1-min streamer / resample queries.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_daily (
            symbol        LowCardinality(String),
            timestamp     DateTime64(3, 'UTC'),
            open          Float64,
            high          Float64,
            low           Float64,
            close         Float64,
            volume        Float64,
            source        LowCardinality(String) DEFAULT '',
            version       UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYear(timestamp)
        ORDER BY (symbol, timestamp)
        SETTINGS index_granularity = 8192
        """
    )

    client.command(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id                UUID DEFAULT generateUUIDv4(),
            symbol            LowCardinality(String),
            signal_type       LowCardinality(String),
            indicator         LowCardinality(String),
            ts_signal         DateTime64(3, 'UTC'),
            price_at_signal   Float64,
            indicator_value   Float64,
            p1_ts             DateTime64(3, 'UTC'),
            p2_ts             DateTime64(3, 'UTC')
        )
        ENGINE = MergeTree()
        PARTITION BY toYYYYMM(ts_signal)
        ORDER BY (symbol, ts_signal, id)
        SETTINGS index_granularity = 8192
        """
    )

    # Watchlists: soft-deleted via `is_active`. We never DROP rows so an LLM/agent
    # can later query "what was in this watchlist on 2026-05-01?". `kind` lets us
    # distinguish user-created lists from the always-streaming 'baseline' and
    # the auto-managed 'adhoc' list of symbol-page visits.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS watchlists (
            name        LowCardinality(String),
            kind        LowCardinality(String) DEFAULT 'user',
            description String DEFAULT '',
            is_active   UInt8 DEFAULT 1,
            updated_at  DateTime64(3, 'UTC') DEFAULT now64(3),
            version     UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (name)
        SETTINGS index_granularity = 8192
        """
    )

    client.command(
        """
        CREATE TABLE IF NOT EXISTS watchlist_members (
            watchlist_name LowCardinality(String),
            symbol         LowCardinality(String),
            is_active      UInt8 DEFAULT 1,
            updated_at     DateTime64(3, 'UTC') DEFAULT now64(3),
            version        UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (watchlist_name, symbol)
        SETTINGS index_granularity = 8192
        """
    )


def _read_legacy_watchlist(path: str) -> Optional[list[str]]:
    """Best-effort read of the old `data/watchlist.json` file. Returns None on any error."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        raw = data.get("symbols", []) if isinstance(data, dict) else data
        return [str(s).strip().upper() for s in raw if str(s).strip()]
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("migrate_default_watchlist: could not read %s: %s", path, e)
        return None


def migrate_default_watchlist(json_path: str = LEGACY_WATCHLIST_JSON) -> dict:
    """
    One-shot migration: if `watchlists` is empty and `data/watchlist.json` exists,
    seed a `default` user watchlist with those symbols. Idempotent: a second call
    is a no-op because `watchlists` will no longer be empty.

    Returns a small audit dict so callers/tests can verify what happened.
    """
    # Import inside the function to avoid a circular import at module load time
    # (watchlist_repo imports from app.db.client which is imported above).
    from app.db import watchlist_repo

    existing = watchlist_repo.list_watchlists(include_inactive=True)
    if existing:
        return {"migrated": False, "reason": "watchlists table not empty", "count": len(existing)}

    symbols = _read_legacy_watchlist(json_path) or []
    watchlist_repo.create_watchlist(
        DEFAULT_WATCHLIST_NAME,
        kind="user",
        description="Migrated from data/watchlist.json on first startup.",
    )
    if symbols:
        watchlist_repo.add_members(DEFAULT_WATCHLIST_NAME, symbols)
        logger.info(
            "migrate_default_watchlist: seeded '%s' with %d symbols from %s",
            DEFAULT_WATCHLIST_NAME, len(symbols), json_path,
        )
    else:
        logger.info(
            "migrate_default_watchlist: created empty '%s' (no legacy file at %s)",
            DEFAULT_WATCHLIST_NAME, json_path,
        )
    return {
        "migrated": True,
        "watchlist": DEFAULT_WATCHLIST_NAME,
        "symbols": symbols,
        "source": json_path if symbols else None,
    }
