"""
Unit tests for app.services.backfill_service.BackfillService.

We exercise the service against:
  - a `FakeLoader` that records every `load_bars` call and returns a fixed
    number of dummy rows (so we can assert "how many fetches", "what window",
    etc.),
  - an injected `coverage_fn` so we can simulate "DB already has X bars"
    without touching ClickHouse,
  - an injected `now_fn` so windows are deterministic.

No ClickHouse, no provider; pure logic tests.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import pytest

from app.services.backfill_service import BackfillService


# ---------- Fakes ----------


class FakeLoader:
    """
    Replaces the parts of HistoricalDataLoader that BackfillService actually
    touches: `_fetch_from_provider` (where the provider call would happen)
    and `_persist` is monkey-patched away on the service itself.
    """

    def __init__(self, bars_per_call: int = 1000) -> None:
        self.bars_per_call = bars_per_call
        self.calls: list[dict] = []

    async def _fetch_from_provider(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        self.calls.append({"symbol": symbol, "start": start, "end": end})
        # `_persist` walks `df.iterrows()` and reads OHLCV columns. Return a
        # DataFrame with a DatetimeIndex so that `ts.tzinfo` works as expected
        # in the real `_persist` (we patch around it in tests though).
        idx = pd.date_range(start=start, periods=self.bars_per_call, freq="1min", tz=timezone.utc)
        return pd.DataFrame(
            {
                "open": [1.0] * self.bars_per_call,
                "high": [1.0] * self.bars_per_call,
                "low": [1.0] * self.bars_per_call,
                "close": [1.0] * self.bars_per_call,
                "volume": [1.0] * self.bars_per_call,
            },
            index=idx,
        )


def _ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------- Fixtures ----------


@pytest.fixture
def fixed_now():
    return _ts(2026, 5, 12, 17)


@pytest.fixture
def empty_coverage_fn():
    async def _fn(symbol, start, end):
        return {
            "symbol": symbol.upper(),
            "start": start,
            "end": end,
            "earliest": None,
            "latest": None,
            "bar_count": 0,
        }
    return _fn


def make_coverage_fn(bar_count: int, earliest: Optional[datetime], latest: Optional[datetime]):
    async def _fn(symbol, start, end):
        return {
            "symbol": symbol.upper(),
            "start": start,
            "end": end,
            "earliest": earliest,
            "latest": latest,
            "bar_count": bar_count,
        }
    return _fn


async def _noop_persist(self, symbol, df):  # bound replacement for BackfillService._persist
    return None


@pytest.fixture(autouse=True)
def _patch_persist(monkeypatch):
    """All tests: skip ClickHouse writes inside BackfillService."""
    monkeypatch.setattr(BackfillService, "_persist", _noop_persist)


# ---------- Quick path ----------


@pytest.mark.asyncio
async def test_quick_short_circuits_when_coverage_is_high(fixed_now, empty_coverage_fn) -> None:
    # 30 days * 5/7 trading days * 390 min ~= 8357 expected; supply 9000 (>90%).
    cov_fn = make_coverage_fn(bar_count=9000, earliest=fixed_now - timedelta(days=30), latest=fixed_now)
    loader = FakeLoader(bars_per_call=500)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    await asyncio.sleep(0)  # let the task tick
    await asyncio.sleep(0)
    # Drain any pending background tasks for this symbol.
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]
    assert st["quick"]["state"] == "skipped"
    assert "already" in (st["quick"]["reason"] or "")
    # Loader should not have been called - we short-circuited.
    assert loader.calls == []


@pytest.mark.asyncio
async def test_quick_fetches_when_coverage_is_low(fixed_now) -> None:
    cov_fn = make_coverage_fn(bar_count=10, earliest=fixed_now - timedelta(hours=2), latest=fixed_now)
    loader = FakeLoader(bars_per_call=8000)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    for _ in range(3):
        await asyncio.sleep(0)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]
    assert st["quick"]["state"] == "done"
    assert st["quick"]["bars"] == 8000
    assert len(loader.calls) == 1
    # 30-day window relative to fixed_now
    assert loader.calls[0]["end"] == fixed_now
    assert loader.calls[0]["start"] == fixed_now - timedelta(days=30)


@pytest.mark.asyncio
async def test_enqueue_quick_is_idempotent(fixed_now, empty_coverage_fn) -> None:
    loader = FakeLoader(bars_per_call=1)
    # Add a tiny artificial delay so the first task is observably still running.
    original = loader._fetch_from_provider

    async def slow_load(*a, **kw):
        await asyncio.sleep(0.05)
        return await original(*a, **kw)

    loader._fetch_from_provider = slow_load  # type: ignore[assignment]

    svc = BackfillService(loader=loader, coverage_fn=empty_coverage_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    first = svc.enqueue_quick("AAPL", days=30)
    # Yield to let the task progress past `state=queued` into the slow load.
    await asyncio.sleep(0)
    second = svc.enqueue_quick("AAPL", days=30)  # should NOT spawn a second task
    assert first["state"] == "queued"
    assert second["state"] in ("queued", "running")
    assert second.get("reason") == "already running" or first == second

    for task in list(svc._tasks.values()):
        await task

    # Only one underlying loader call despite two enqueues.
    assert len(loader.calls) == 1


# ---------- Deep path ----------


@pytest.mark.asyncio
async def test_deep_chunks_full_window_when_db_is_empty(fixed_now, empty_coverage_fn) -> None:
    loader = FakeLoader(bars_per_call=2000)
    svc = BackfillService(
        loader=loader,  # type: ignore[arg-type]
        coverage_fn=empty_coverage_fn,
        now_fn=lambda: fixed_now,
        chunk_days=9,
    )

    svc.enqueue_deep("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["deep"]
    assert st["state"] == "done"
    # 30 days / 9-day chunks -> 4 chunks (9, 9, 9, 3)
    assert st["chunks_total"] == 4
    assert st["chunks_done"] == 4
    assert len(loader.calls) == 4
    # Walk chunks: each load_bars call has both `start` and `end` set (range mode).
    for call in loader.calls:
        assert call["start"] is not None
        assert call["end"] is not None
        span_days = (call["end"] - call["start"]).total_seconds() / 86400.0
        assert span_days <= 9.0 + 1e-6
    # Chunks should tile [fixed_now-30d, fixed_now] without overlap or gap
    # (the loader walks newest-to-oldest).
    sorted_calls = sorted(loader.calls, key=lambda c: c["start"])
    earliest = sorted_calls[0]["start"]
    latest = sorted_calls[-1]["end"]
    assert earliest == fixed_now - timedelta(days=30)
    assert latest == fixed_now


@pytest.mark.asyncio
async def test_deep_skipped_when_history_already_covers_target(fixed_now) -> None:
    # DB already has bars going back further than the target window.
    cov_fn = make_coverage_fn(
        bar_count=12345,
        earliest=fixed_now - timedelta(days=400),
        latest=fixed_now,
    )
    loader = FakeLoader(bars_per_call=1000)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_deep("AAPL", days=365)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["deep"]
    assert st["state"] == "skipped"
    assert "no gap" in (st["reason"] or "")
    assert loader.calls == []


@pytest.mark.asyncio
async def test_deep_fetches_only_missing_gap(fixed_now) -> None:
    # DB has data starting 100 days ago. Target is 365 days. Gap = days 100..365.
    earliest_in_db = fixed_now - timedelta(days=100)
    cov_fn = make_coverage_fn(bar_count=50000, earliest=earliest_in_db, latest=fixed_now)
    loader = FakeLoader(bars_per_call=2000)
    svc = BackfillService(
        loader=loader,  # type: ignore[arg-type]
        coverage_fn=cov_fn,
        now_fn=lambda: fixed_now,
        chunk_days=30,  # bigger chunk so the test is fast
    )

    svc.enqueue_deep("AAPL", days=365)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["deep"]
    assert st["state"] == "done"
    # Gap is 265 days, chunked by 30 days -> ceil(265/30) = 9 chunks.
    assert st["chunks_total"] == 9
    assert st["chunks_done"] == 9
    assert len(loader.calls) == 9

    # Highest `end` should be the existing earliest (we filled the gap backwards).
    max_end = max(c["end"] for c in loader.calls)
    min_start = min(c["start"] for c in loader.calls)
    assert max_end == earliest_in_db
    assert min_start == fixed_now - timedelta(days=365)


# ---------- Misc ----------


@pytest.mark.asyncio
async def test_coverage_report_shape(fixed_now) -> None:
    cov_fn = make_coverage_fn(
        bar_count=1000,
        earliest=fixed_now - timedelta(days=5),
        latest=fixed_now,
    )
    loader = FakeLoader()
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    report = await svc.coverage("AAPL", days=30)
    assert report["symbol"] == "AAPL"
    assert report["window_days"] == 30
    assert report["bar_count"] == 1000
    assert isinstance(report["expected_approx"], int)
    assert 0.0 <= report["ratio"] <= 1.0


@pytest.mark.asyncio
async def test_empty_symbol_is_an_error(fixed_now, empty_coverage_fn) -> None:
    loader = FakeLoader()
    svc = BackfillService(loader=loader, coverage_fn=empty_coverage_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]
    result = svc.enqueue_quick("", days=30)
    assert result["state"] == "error"
    assert "empty" in result["error"]


# ---------- Intraday (5m) path ----------


class FakeProvider:
    """Stand-in for `loader.provider` used by the intraday + daily paths."""
    def __init__(self, df_to_return) -> None:
        self._df = df_to_return
        self.calls: list[dict] = []

    async def historical_df(self, symbol, start, end, *, timeframe: str = "1Min"):
        self.calls.append({"symbol": symbol, "start": start, "end": end, "timeframe": timeframe})
        return self._df


def _make_5m_df(start: datetime, count: int) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=count, freq="5min", tz=timezone.utc)
    return pd.DataFrame(
        {
            "open": [1.0] * count,
            "high": [1.0] * count,
            "low": [1.0] * count,
            "close": [1.0] * count,
            "volume": [1.0] * count,
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_intraday_fetches_when_5m_table_empty(fixed_now, monkeypatch) -> None:
    # Empty 5m coverage -> backfill fires.
    async def empty_5m_cov(symbol, start, end):
        return {"symbol": symbol.upper(), "start": start, "end": end,
                "earliest": None, "latest": None, "bar_count": 0}
    monkeypatch.setattr("app.db.queries.coverage_5m", lambda *a, **k: None)
    # Patch the async helper used by _execute_intraday
    from app.db import queries as q
    monkeypatch.setattr(q, "coverage_5m", lambda sym, s, e: {
        "symbol": sym.upper(), "start": s, "end": e,
        "earliest": None, "latest": None, "bar_count": 0,
    })

    df = _make_5m_df(fixed_now - timedelta(days=270), count=100)
    provider = FakeProvider(df)
    loader = FakeLoader()
    loader.provider = provider  # type: ignore[attr-defined]
    svc = BackfillService(loader=loader, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    # Stub the 5m persist so we don't touch ClickHouse from a unit test.
    persisted: list[int] = []

    async def fake_persist_5m(self, symbol, df):
        persisted.append(len(df))

    monkeypatch.setattr(BackfillService, "_persist_5m", fake_persist_5m)

    svc.enqueue_intraday("AAPL", days=270)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["intraday"]
    assert st["state"] == "done"
    assert st["bars"] == 100
    assert len(provider.calls) == 1
    assert provider.calls[0]["timeframe"] == "5m"
    # Provider should have been asked for the full 270d window.
    assert provider.calls[0]["start"] == fixed_now - timedelta(days=270)
    assert provider.calls[0]["end"] == fixed_now
    assert persisted == [100]


@pytest.mark.asyncio
async def test_intraday_skipped_when_5m_table_already_covers(fixed_now, monkeypatch) -> None:
    from app.db import queries as q
    # Existing coverage older than target start - should short-circuit.
    monkeypatch.setattr(q, "coverage_5m", lambda sym, s, e: {
        "symbol": sym.upper(), "start": s, "end": e,
        "earliest": fixed_now - timedelta(days=280),
        "latest": fixed_now,
        "bar_count": 9999,
    })

    provider = FakeProvider(_make_5m_df(fixed_now, count=1))
    loader = FakeLoader()
    loader.provider = provider  # type: ignore[attr-defined]
    svc = BackfillService(loader=loader, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_intraday("AAPL", days=270)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["intraday"]
    assert st["state"] == "skipped"
    assert "already covers" in (st["reason"] or "")
    # Provider should NOT have been called.
    assert provider.calls == []
