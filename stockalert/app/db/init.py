"""Idempotent ClickHouse DDL (safe to run on every startup)."""
from __future__ import annotations

from app.config import settings
from app.db.client import get_admin_client, get_client


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
