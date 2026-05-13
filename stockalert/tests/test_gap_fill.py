"""
Integration tests for the gap-detection + gap-fill pipeline:
  - `queries.find_intraday_gaps` against real ClickHouse, with deterministic
    seeded data.
  - `BackfillService._merge_gap_ranges` pure-Python helper.
  - `BackfillService._execute_gap_fill` end-to-end with a fake provider that
    returns the missing bars, verifying:
       * gaps detected and merged correctly,
       * persisted bars are actually inserted into the source table,
       * job status transitions from running -> done,
       * idempotency: a second run reports `skipped` (no remaining gaps).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

import pandas as pd
import pytest

from app.db import queries
from app.db.client import get_client
from app.services.backfill_service import BackfillService


TEST_SYMBOL_PREFIX = "__TEST_GAPS_"
TEST_SYMBOL = TEST_SYMBOL_PREFIX + "AAPL"


def _wipe() -> None:
    client = get_client()
    for table in ("ohlcv_1m", "ohlcv_5m"):
        rows = client.query(
            f"SELECT DISTINCT symbol FROM {table} WHERE symbol LIKE '{TEST_SYMBOL_PREFIX}%'"
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
    _wipe()
    yield
    _wipe()


def _seed_1m(symbol: str, timestamps: list[datetime]) -> None:
    """Insert 1-min bars at the exact given timestamps. Used to construct gaps."""
    rows = [
        {
            "symbol": symbol,
            "timestamp": ts,
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
            "volume": 1.0, "vwap": 0.0, "trade_count": 0, "source": "test",
        }
        for ts in timestamps
    ]
    queries.insert_bars_batch(rows)


# ---------- find_intraday_gaps query ----------


def test_no_gaps_returns_empty(fresh_db) -> None:
    """Consecutive minute bars -> zero gaps."""
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    _seed_1m(TEST_SYMBOL, [start + timedelta(minutes=i) for i in range(10)])

    gaps = queries.find_intraday_gaps(
        TEST_SYMBOL,
        start - timedelta(minutes=1),
        start + timedelta(minutes=20),
    )
    assert gaps == []


def test_single_within_session_gap(fresh_db) -> None:
    """3-minute hole between 14:35 and 14:39 should produce one gap of 3 missing bars."""
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    timestamps = (
        [start + timedelta(minutes=i) for i in range(6)]      # 14:30..14:35
        + [start + timedelta(minutes=i) for i in range(9, 15)]  # 14:39..14:44
    )
    _seed_1m(TEST_SYMBOL, timestamps)

    gaps = queries.find_intraday_gaps(
        TEST_SYMBOL,
        start - timedelta(minutes=1),
        start + timedelta(minutes=20),
    )
    assert len(gaps) == 1
    g = gaps[0]
    assert g["prev_ts"] == start + timedelta(minutes=5)   # 14:35
    assert g["next_ts"] == start + timedelta(minutes=9)   # 14:39
    assert g["missing"] == 3                              # 14:36, 14:37, 14:38
    # tz-aware
    assert g["prev_ts"].tzinfo is not None
    assert g["next_ts"].tzinfo is not None


def test_overnight_boundary_is_not_a_gap(fresh_db) -> None:
    """A 16-hour overnight gap (> 4h boundary) must be filtered out."""
    day1 = datetime(2026, 4, 15, 19, 55, tzinfo=timezone.utc)
    day2 = datetime(2026, 4, 16, 13, 30, tzinfo=timezone.utc)  # ~17.5h later
    timestamps = (
        [day1 + timedelta(minutes=i) for i in range(5)]   # 19:55..19:59
        + [day2 + timedelta(minutes=i) for i in range(5)]  # 13:30..13:34
    )
    _seed_1m(TEST_SYMBOL, timestamps)

    gaps = queries.find_intraday_gaps(
        TEST_SYMBOL,
        day1 - timedelta(minutes=1),
        day2 + timedelta(minutes=10),
    )
    assert gaps == [], "overnight boundary (>4h) must NOT be reported as a gap"


def test_multiple_gaps_ordered_chronologically(fresh_db) -> None:
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    timestamps = (
        [start + timedelta(minutes=0)]                   # 14:30
        + [start + timedelta(minutes=5)]                  # 14:35  (gap of 4)
        + [start + timedelta(minutes=10)]                 # 14:40  (gap of 4)
        + [start + timedelta(minutes=11)]                 # 14:41  (no gap)
    )
    _seed_1m(TEST_SYMBOL, timestamps)

    gaps = queries.find_intraday_gaps(
        TEST_SYMBOL, start - timedelta(minutes=1), start + timedelta(minutes=20),
    )
    assert [g["missing"] for g in gaps] == [4, 4]
    assert gaps[0]["prev_ts"] < gaps[1]["prev_ts"]


def test_unsupported_source_raises(fresh_db) -> None:
    with pytest.raises(ValueError):
        queries.find_intraday_gaps(
            TEST_SYMBOL, datetime.now(timezone.utc) - timedelta(days=1),
            datetime.now(timezone.utc), source_table="ohlcv_garbage",
        )


# ---------- merge_gap_ranges (pure Python) ----------


def test_merge_collapses_adjacent_ranges() -> None:
    t = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    gaps = [
        {"prev_ts": t + timedelta(minutes=0),  "next_ts": t + timedelta(minutes=5),  "missing": 4},
        # 10-min apart - within default 30-min merge threshold
        {"prev_ts": t + timedelta(minutes=15), "next_ts": t + timedelta(minutes=20), "missing": 4},
        # 60-min later - separate range
        {"prev_ts": t + timedelta(minutes=80), "next_ts": t + timedelta(minutes=85), "missing": 4},
    ]
    ranges = BackfillService._merge_gap_ranges(gaps, merge_minutes=30)
    assert len(ranges) == 2
    assert ranges[0] == (t + timedelta(minutes=0), t + timedelta(minutes=20))
    assert ranges[1] == (t + timedelta(minutes=80), t + timedelta(minutes=85))


def test_merge_keeps_distant_ranges_separate() -> None:
    t = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    gaps = [
        {"prev_ts": t + timedelta(minutes=0),   "next_ts": t + timedelta(minutes=5),   "missing": 4},
        {"prev_ts": t + timedelta(minutes=60),  "next_ts": t + timedelta(minutes=65),  "missing": 4},
        {"prev_ts": t + timedelta(minutes=120), "next_ts": t + timedelta(minutes=125), "missing": 4},
    ]
    ranges = BackfillService._merge_gap_ranges(gaps, merge_minutes=5)
    assert len(ranges) == 3


def test_merge_empty_input() -> None:
    assert BackfillService._merge_gap_ranges([], merge_minutes=30) == []


# ---------- _execute_gap_fill end-to-end ----------


class FakeProvider:
    """Returns a deterministic DataFrame for any window asked of it."""
    def __init__(self, df_to_return: pd.DataFrame) -> None:
        self._df = df_to_return
        self.calls: list[tuple[datetime, datetime]] = []

    async def historical_df(self, symbol, start, end, *, timeframe: str = "1Min"):
        self.calls.append((start, end))
        # Trim to the requested window so we mimic a real provider.
        return self._df[(self._df.index >= start) & (self._df.index <= end)].copy()


class FakeLoader:
    """Mimics enough of HistoricalDataLoader for the gap-fill path."""
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def _fetch_from_provider(self, symbol, start, end):
        return await self.provider.historical_df(symbol, start, end)


def _build_missing_bars(start: datetime, n: int) -> pd.DataFrame:
    """Generate n minute bars starting at `start`."""
    idx = pd.date_range(start=start, periods=n, freq="1min", tz=timezone.utc)
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0},
        index=idx,
    )


@pytest.mark.asyncio
async def test_gap_fill_repairs_a_single_gap(fresh_db) -> None:
    """A single 3-minute hole should be detected, fetched, and refilled."""
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    # Seed with a 3-min hole between 14:35 and 14:39
    _seed_1m(
        TEST_SYMBOL,
        [start + timedelta(minutes=i) for i in range(6)]
        + [start + timedelta(minutes=i) for i in range(9, 15)],
    )
    # Sanity: gap exists
    pre_gaps = queries.find_intraday_gaps(
        TEST_SYMBOL, start - timedelta(minutes=1), start + timedelta(minutes=20),
    )
    assert len(pre_gaps) == 1
    assert pre_gaps[0]["missing"] == 3

    # Fake provider returns the 3 missing bars + a few neighbors
    missing_df = _build_missing_bars(start + timedelta(minutes=6), 3)  # 14:36, 14:37, 14:38
    provider = FakeProvider(missing_df)
    loader = FakeLoader(provider)
    # Fix the now-fn so target window includes our seeded data.
    fixed_now = start + timedelta(minutes=20)
    svc = BackfillService(loader=loader, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_gap_fill(TEST_SYMBOL, days=1, source="ohlcv_1m")
    for task in list(svc._tasks.values()):
        await task

    # 1 provider call (one merged range)
    assert len(provider.calls) == 1

    # Job status finished with bars persisted and 0 gaps remaining
    st = svc.status(TEST_SYMBOL)[TEST_SYMBOL]["gap_fill"]
    assert st["state"] == "done"
    assert st["bars"] == 3

    post_gaps = queries.find_intraday_gaps(
        TEST_SYMBOL, start - timedelta(minutes=1), start + timedelta(minutes=20),
    )
    assert post_gaps == [], "gap should be filled after gap_fill"


@pytest.mark.asyncio
async def test_gap_fill_skipped_when_no_gaps(fresh_db) -> None:
    """No gaps -> short-circuit without touching the provider."""
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    _seed_1m(TEST_SYMBOL, [start + timedelta(minutes=i) for i in range(20)])

    provider = FakeProvider(pd.DataFrame())
    loader = FakeLoader(provider)
    fixed_now = start + timedelta(minutes=30)
    svc = BackfillService(loader=loader, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_gap_fill(TEST_SYMBOL, days=1, source="ohlcv_1m")
    for task in list(svc._tasks.values()):
        await task

    assert provider.calls == []
    st = svc.status(TEST_SYMBOL)[TEST_SYMBOL]["gap_fill"]
    assert st["state"] == "skipped"
    assert "no within-session gaps" in (st["reason"] or "")


@pytest.mark.asyncio
async def test_gap_fill_merges_close_gaps_into_single_fetch(fresh_db) -> None:
    """Two gaps 10 minutes apart should produce ONE merged provider call."""
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    # Two gaps within ~30 min of each other, so they should merge.
    # Have: 0..5  9..14  18..25  -> gaps at [5,9] and [14,18]
    _seed_1m(
        TEST_SYMBOL,
        [start + timedelta(minutes=i) for i in range(6)]
        + [start + timedelta(minutes=i) for i in range(9, 15)]
        + [start + timedelta(minutes=i) for i in range(18, 26)],
    )

    # Cover ALL missing minutes
    missing_df = pd.concat([
        _build_missing_bars(start + timedelta(minutes=6),  3),  # fills 6,7,8
        _build_missing_bars(start + timedelta(minutes=15), 3),  # fills 15,16,17
    ])
    provider = FakeProvider(missing_df)
    loader = FakeLoader(provider)
    fixed_now = start + timedelta(minutes=40)
    svc = BackfillService(loader=loader, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_gap_fill(TEST_SYMBOL, days=1, source="ohlcv_1m")
    for task in list(svc._tasks.values()):
        await task

    # Single merged range -> one provider call
    assert len(provider.calls) == 1
    st = svc.status(TEST_SYMBOL)[TEST_SYMBOL]["gap_fill"]
    assert st["state"] == "done"

    post_gaps = queries.find_intraday_gaps(
        TEST_SYMBOL, start - timedelta(minutes=1), start + timedelta(minutes=30),
    )
    assert post_gaps == []
