"""
Integration tests for `queries.list_bars_resampled`.

Inserts a deterministic minute-by-minute 1-min bar series into ClickHouse under
a test-only symbol prefix, then asserts that resampling at 5m / 15m / 1h / 1d
produces correctly aggregated OHLCV values:

  - open  = first 1-min open within the bucket
  - high  = max of 1-min highs
  - low   = min of 1-min lows
  - close = last 1-min close within the bucket
  - volume = sum of 1-min volumes

All inserts are under symbols prefixed with `__test_rs_` so a cleanup fixture
can safely delete them without touching real data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from app.db import queries
from app.db.client import get_client


# Use an all-uppercase prefix because read queries (`list_bars_resampled`,
# `coverage`, `latest_bar_per_symbol`) call `.upper()` on the symbol; mixed-case
# test prefixes would silently mismatch the inserted lowercase rows.
TEST_SYMBOL_PREFIX = "__TEST_RS_"
TEST_SYMBOL = TEST_SYMBOL_PREFIX + "AAPL"


# ---------- Fixtures ----------


def _wipe_test_symbols() -> None:
    """
    Hard-delete only rows under our test prefix from BOTH the 1-min and 5-min
    tables, synchronously. ClickHouse `ALTER ... DELETE` is normally async;
    we force `mutations_sync=2` so the delete is durable before the next test.

    Safety: refuses to run if a stray real symbol slipped through.
    """
    client = get_client()
    for table in ("ohlcv_1m", "ohlcv_5m"):
        rows = client.query(
            f"SELECT DISTINCT symbol FROM {table} "
            f"WHERE symbol LIKE '{TEST_SYMBOL_PREFIX}%'"
        ).result_rows
        bad = [r[0] for r in rows if not r[0].startswith(TEST_SYMBOL_PREFIX)]
        if bad:
            raise AssertionError(f"refusing to delete non-test rows from {table}: {bad}")
        client.command(
            f"ALTER TABLE {table} DELETE WHERE symbol LIKE %(p)s",
            parameters={"p": f"{TEST_SYMBOL_PREFIX}%"},
            settings={"mutations_sync": 2},
        )


@pytest.fixture
def fresh_db(clickhouse_ready) -> Iterator[None]:
    _wipe_test_symbols()
    yield
    _wipe_test_symbols()


def _seed_minute_bars(
    symbol: str,
    start: datetime,
    minutes: int,
    *,
    base_price: float = 100.0,
) -> None:
    """
    Insert `minutes` consecutive 1-min bars starting at `start`. Each bar's
    OHLCV is deterministic so tests can assert exact aggregations:
      - open = base_price + i        (so first-open = base_price)
      - high = open + 1
      - low  = open - 1
      - close = open + 0.5
      - volume = 100 * (i + 1)
    """
    rows = []
    for i in range(minutes):
        ts = start + timedelta(minutes=i)
        o = base_price + i
        rows.append(
            {
                "symbol": symbol,
                "timestamp": ts,
                "open": o,
                "high": o + 1.0,
                "low": o - 1.0,
                "close": o + 0.5,
                "volume": 100 * (i + 1),
                "vwap": 0.0,
                "trade_count": 0,
                "source": "test",
            }
        )
    queries.insert_bars_batch(rows)


# ---------- Tests ----------


def test_1m_passthrough_matches_list_bars_desc(fresh_db) -> None:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)  # Monday 9:30 ET
    _seed_minute_bars(TEST_SYMBOL, start, minutes=10)

    end = start + timedelta(minutes=20)
    bars = queries.list_bars_resampled(TEST_SYMBOL, "1m", start, end, limit=100)
    assert len(bars) == 10
    assert bars[0]["open"] == 100.0
    assert bars[-1]["close"] == 100.0 + 9 + 0.5


def test_5m_aggregation_groups_into_buckets(fresh_db) -> None:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    _seed_minute_bars(TEST_SYMBOL, start, minutes=15)

    bars = queries.list_bars_resampled(TEST_SYMBOL, "5m", start, start + timedelta(minutes=60), limit=100)
    assert len(bars) == 3

    # Bucket 1: minutes 0..4 -> open=100, close=104.5, high=105, low=99, vol=100+200+300+400+500
    b0 = bars[0]
    assert b0["open"] == pytest.approx(100.0)
    assert b0["close"] == pytest.approx(104.5)
    assert b0["high"] == pytest.approx(105.0)
    assert b0["low"] == pytest.approx(99.0)
    assert b0["volume"] == sum(100 * (i + 1) for i in range(5))

    # Bucket 2: minutes 5..9 -> open=105, close=109.5, high=110, low=104
    b1 = bars[1]
    assert b1["open"] == pytest.approx(105.0)
    assert b1["close"] == pytest.approx(109.5)
    assert b1["high"] == pytest.approx(110.0)
    assert b1["low"] == pytest.approx(104.0)

    # Bucket 3: minutes 10..14 -> open=110, close=114.5
    b2 = bars[2]
    assert b2["open"] == pytest.approx(110.0)
    assert b2["close"] == pytest.approx(114.5)


def test_15m_aggregation_single_bucket(fresh_db) -> None:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    _seed_minute_bars(TEST_SYMBOL, start, minutes=15)

    bars = queries.list_bars_resampled(TEST_SYMBOL, "15m", start, start + timedelta(hours=1), limit=100)
    assert len(bars) == 1
    b = bars[0]
    assert b["open"] == pytest.approx(100.0)
    assert b["close"] == pytest.approx(114.5)
    assert b["high"] == pytest.approx(115.0)
    assert b["low"] == pytest.approx(99.0)
    assert b["volume"] == sum(100 * (i + 1) for i in range(15))


def test_1h_aggregation_spans_multiple_buckets(fresh_db) -> None:
    start = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)  # exactly on hour boundary
    _seed_minute_bars(TEST_SYMBOL, start, minutes=120)  # 2 hours

    bars = queries.list_bars_resampled(TEST_SYMBOL, "1h", start, start + timedelta(hours=3), limit=100)
    assert len(bars) == 2
    # Bucket 0: 60 1-min bars -> opens at 100, closes at 100+59+0.5=159.5
    assert bars[0]["open"] == pytest.approx(100.0)
    assert bars[0]["close"] == pytest.approx(159.5)
    # Bucket 1: 60 more -> opens at 160, closes at 100+119+0.5=219.5
    assert bars[1]["open"] == pytest.approx(160.0)
    assert bars[1]["close"] == pytest.approx(219.5)


def test_limit_clips_oldest_first(fresh_db) -> None:
    """`limit=N` keeps the N MOST RECENT bars, then we re-sort ascending."""
    start = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    _seed_minute_bars(TEST_SYMBOL, start, minutes=15)
    # 15 1-min bars => 3 5-min buckets. Ask for 2.
    bars = queries.list_bars_resampled(TEST_SYMBOL, "5m", start, start + timedelta(hours=1), limit=2)
    assert len(bars) == 2
    # We dropped the earliest bucket (14:00..14:04) and kept the two later ones.
    assert bars[0]["open"] == pytest.approx(105.0)  # bucket at 14:05
    assert bars[1]["open"] == pytest.approx(110.0)  # bucket at 14:10


def test_invalid_interval_raises() -> None:
    with pytest.raises(ValueError):
        queries.list_bars_resampled("AAPL", "13m", None, None, limit=10)


def test_empty_window_returns_empty(fresh_db) -> None:
    start = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    _seed_minute_bars(TEST_SYMBOL, start, minutes=5)
    # Window is in the future where we seeded nothing.
    bars = queries.list_bars_resampled(
        TEST_SYMBOL, "5m",
        start + timedelta(days=1),
        start + timedelta(days=2),
        limit=10,
    )
    assert bars == []


def test_invalid_source_table_raises(fresh_db) -> None:
    with pytest.raises(ValueError):
        queries.list_bars_resampled(
            TEST_SYMBOL, "5m", None, None, limit=10, source_table="ohlcv_garbage",
        )


def test_1m_interval_rejected_for_5m_source(fresh_db) -> None:
    with pytest.raises(ValueError):
        queries.list_bars_resampled(
            TEST_SYMBOL, "1m", None, None, limit=10, source_table="ohlcv_5m",
        )


def _seed_5m_bars(symbol: str, start: datetime, intervals: int, base_price: float = 100.0) -> None:
    """Seed `intervals` 5-min bars into ohlcv_5m. Deterministic OHLCV so tests
    can assert exact aggregations."""
    rows = []
    for i in range(intervals):
        ts = start + timedelta(minutes=5 * i)
        o = base_price + i
        rows.append({
            "symbol": symbol, "timestamp": ts,
            "open": o, "high": o + 1.0, "low": o - 1.0,
            "close": o + 0.5, "volume": 100 * (i + 1),
            "vwap": 0.0, "trade_count": 0, "source": "test",
        })
    queries.insert_5m_bars_batch(rows)


def test_resample_from_5m_source_passthrough(fresh_db) -> None:
    """Reading 5m from ohlcv_5m with interval='5m' yields the same rows."""
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    _seed_5m_bars(TEST_SYMBOL, start, intervals=6)  # 30 minutes of 5m bars

    bars = queries.list_bars_resampled(
        TEST_SYMBOL, "5m", start, start + timedelta(hours=1), limit=100,
        source_table="ohlcv_5m",
    )
    assert len(bars) == 6
    assert bars[0]["open"] == pytest.approx(100.0)
    assert bars[-1]["close"] == pytest.approx(100.0 + 5 + 0.5)


def test_resample_5m_to_30m_from_5m_source(fresh_db) -> None:
    """30m resampling from a 5m source: 6 5m bars per 30m bucket."""
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    _seed_5m_bars(TEST_SYMBOL, start, intervals=12)  # 1 hour of 5m bars

    bars = queries.list_bars_resampled(
        TEST_SYMBOL, "30m", start, start + timedelta(hours=2), limit=100,
        source_table="ohlcv_5m",
    )
    assert len(bars) == 2
    # Bucket 1: 5m indices 0..5 -> open=100, close=105.5, high=106, low=99
    assert bars[0]["open"] == pytest.approx(100.0)
    assert bars[0]["close"] == pytest.approx(105.5)
    assert bars[0]["high"] == pytest.approx(106.0)
    assert bars[0]["low"] == pytest.approx(99.0)
    # Bucket 2: 5m indices 6..11 -> open=106, close=111.5, high=112, low=105
    assert bars[1]["open"] == pytest.approx(106.0)
    assert bars[1]["close"] == pytest.approx(111.5)


def test_resample_5m_to_1h_from_5m_source(fresh_db) -> None:
    """1h resampling from a 5m source: 12 5m bars per hourly bucket."""
    start = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
    _seed_5m_bars(TEST_SYMBOL, start, intervals=24)  # 2 hours

    bars = queries.list_bars_resampled(
        TEST_SYMBOL, "1h", start, start + timedelta(hours=3), limit=100,
        source_table="ohlcv_5m",
    )
    assert len(bars) == 2
    # Hour 1: indices 0..11 -> open=100, close=100+11+0.5=111.5
    assert bars[0]["open"] == pytest.approx(100.0)
    assert bars[0]["close"] == pytest.approx(111.5)
    # Hour 2: indices 12..23 -> open=112, close=100+23+0.5=123.5
    assert bars[1]["open"] == pytest.approx(112.0)
    assert bars[1]["close"] == pytest.approx(123.5)
