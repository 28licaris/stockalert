"""Unit tests for on-demand chart hydration (the live Schwab tier).

Covers the contract from `app/services/readers/bars_hydration.py`:
  - stored tiers win — no live fetch when CH/lake already have bars
  - a miss → direct Schwab fetch (native frequency per interval), served
    in-process, NO CH/Iceberg write (keeps the request fast + non-blocking)
  - 1h resamples from native 30-min; minute windows clamp to Schwab's ~48d
  - results are TTL-cached; no-data is negative-cached; errors are not
  - single-flight collapses concurrent callers to one upstream fetch
  - non-AUTO sources never trigger a live pull
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import pytest

import app.services.readers.bars_hydration as hyd
from app.services.readers.bars_gateway import BarSource
from app.services.readers.schemas import LiveBar


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test starts with empty single-flight / negative / TTL caches."""
    hyd._reset_caches()
    yield
    hyd._reset_caches()


def _bar(sym="SIDU", interval="5m", close=10.0) -> LiveBar:
    return LiveBar(
        symbol=sym,
        timestamp=datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc),
        open=close, high=close, low=close, close=close, volume=100.0,
        interval=interval,
    )


def _df(timestamps, *, opens=None, highs=None, lows=None, closes=None, vols=None):
    n = len(timestamps)
    return pd.DataFrame(
        {
            "open": opens or [1.0] * n,
            "high": highs or [2.0] * n,
            "low": lows or [0.5] * n,
            "close": closes or [1.5] * n,
            "volume": vols or [10] * n,
        },
        index=pd.to_datetime(timestamps, utc=True),
    )


class _FakeProvider:
    """Records calls; returns a frame from `df_for(timeframe)`."""

    def __init__(self, frame=None, frames=None):
        self._frame = frame
        self._frames = frames or {}
        self.calls = []

    async def historical_df(self, sym, start, end, timeframe="1Min"):
        self.calls.append({"sym": sym, "start": start, "end": end, "tf": timeframe})
        if timeframe in self._frames:
            return self._frames[timeframe]
        return self._frame if self._frame is not None else pd.DataFrame()


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(hyd, "get_chart_bars", lambda *a, **k: [])
    monkeypatch.setattr("app.config.get_provider", lambda name=None: provider)


# ─────────────────────────────────────────────────────────────────────
# Stored tiers win — no live fetch
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stored_bars_skip_live_fetch(monkeypatch):
    monkeypatch.setattr(hyd, "get_chart_bars", lambda *a, **k: [_bar()])

    def _boom(name=None):
        raise AssertionError("provider must not be built when stored tiers hit")

    monkeypatch.setattr("app.config.get_provider", _boom)

    bars = await hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30)
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_non_auto_source_never_hydrates(monkeypatch):
    monkeypatch.setattr(hyd, "get_chart_bars", lambda *a, **k: [])

    def _boom(name=None):
        raise AssertionError("non-AUTO source must not hydrate")

    monkeypatch.setattr("app.config.get_provider", _boom)

    bars = await hyd.get_chart_bars_hydrated(
        "SIDU", interval="5m", lookback_days=30, source=BarSource.CLICKHOUSE,
    )
    assert bars == []


# ─────────────────────────────────────────────────────────────────────
# Direct Schwab fetch per interval (native frequency, served in-process)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intraday_fetches_native_frequency_directly(monkeypatch):
    """5m → fetch Schwab '5Min' and serve directly; no CH re-query needed."""
    frame = _df(["2026-06-01T14:30:00Z", "2026-06-01T14:35:00Z"])
    provider = _FakeProvider(frame)
    _patch_provider(monkeypatch, provider)

    bars = await hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30)

    assert len(bars) == 2
    assert bars[0].source == "schwab-ondemand"
    assert provider.calls[0]["tf"] == "5Min"


@pytest.mark.asyncio
async def test_one_hour_resamples_from_native_30min(monkeypatch):
    """1h has no native Schwab freq → fetch '30Min', resample 2:1 → 1h."""
    # Four 30-min bars spanning two clock hours → two 1h bars.
    frame = _df(
        ["2026-06-01T14:00:00Z", "2026-06-01T14:30:00Z",
         "2026-06-01T15:00:00Z", "2026-06-01T15:30:00Z"],
        highs=[2, 5, 3, 4], lows=[1, 1, 0.5, 2], closes=[1.5, 2, 2.5, 3], vols=[10, 20, 30, 40],
    )
    provider = _FakeProvider(frame)
    _patch_provider(monkeypatch, provider)

    bars = await hyd.get_chart_bars_hydrated("SIDU", interval="1h", lookback_days=180)

    assert provider.calls[0]["tf"] == "30Min"
    assert len(bars) == 2  # resampled to two hourly buckets
    assert bars[0].high == 5 and bars[0].volume == 30  # max/sum within first hour
    assert bars[0].interval == "1h"


@pytest.mark.asyncio
async def test_minute_window_clamped_to_schwab_reach(monkeypatch):
    """A 60-day 15m request clamps to Schwab's ~48-day minute reach."""
    provider = _FakeProvider(_df(["2026-06-01T14:00:00Z"]))
    _patch_provider(monkeypatch, provider)

    await hyd.get_chart_bars_hydrated("SIDU", interval="15m", lookback_days=60)

    call = provider.calls[0]
    span_days = (call["end"] - call["start"]).days
    assert span_days <= hyd._SCHWAB_MINUTE_REACH_DAYS


@pytest.mark.asyncio
async def test_daily_uses_native_daily_and_full_window(monkeypatch):
    provider = _FakeProvider(_df(["2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z"]))
    _patch_provider(monkeypatch, provider)

    bars = await hyd.get_chart_bars_hydrated("SIDU", interval="1d", lookback_days=365)

    assert len(bars) == 2
    call = provider.calls[0]
    assert call["tf"] == "1d"
    assert (call["end"] - call["start"]).days >= 360  # daily not clamped


# ─────────────────────────────────────────────────────────────────────
# Caching: TTL reuse, negative cache, limit
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_result_is_ttl_cached(monkeypatch):
    provider = _FakeProvider(_df(["2026-06-01T14:30:00Z", "2026-06-01T14:35:00Z"]))
    _patch_provider(monkeypatch, provider)

    first = await hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30)
    second = await hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30)

    assert len(first) == 2 and len(second) == 2
    assert len(provider.calls) == 1  # second served from TTL cache


@pytest.mark.asyncio
async def test_no_data_is_negative_cached(monkeypatch):
    provider = _FakeProvider(pd.DataFrame())  # Schwab returns nothing
    _patch_provider(monkeypatch, provider)

    assert await hyd.get_chart_bars_hydrated("ZZZZ", interval="5m", lookback_days=30) == []
    assert await hyd.get_chart_bars_hydrated("ZZZZ", interval="5m", lookback_days=30) == []
    assert len(provider.calls) == 1  # negative-cached after the first miss


@pytest.mark.asyncio
async def test_fetch_error_is_not_cached(monkeypatch):
    monkeypatch.setattr(hyd, "get_chart_bars", lambda *a, **k: [])
    calls = {"n": 0}

    class Boom:
        async def historical_df(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("schwab 500")

    monkeypatch.setattr("app.config.get_provider", lambda name=None: Boom())

    assert await hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30) == []
    assert await hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30) == []
    assert calls["n"] == 2  # transient error → retried, not cached


@pytest.mark.asyncio
async def test_limit_is_newest_anchored(monkeypatch):
    provider = _FakeProvider(_df(
        ["2026-06-01T14:30:00Z", "2026-06-01T14:35:00Z", "2026-06-01T14:40:00Z"],
        closes=[1, 2, 3],
    ))
    _patch_provider(monkeypatch, provider)

    bars = await hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30, limit=2)
    assert [b.close for b in bars] == [2, 3]  # last two of three


# ─────────────────────────────────────────────────────────────────────
# Single-flight
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_flight_collapses_concurrent(monkeypatch):
    monkeypatch.setattr(hyd, "get_chart_bars", lambda *a, **k: [])
    calls = {"n": 0}
    gate = asyncio.Event()

    class Slow:
        async def historical_df(self, *a, **k):
            calls["n"] += 1
            await gate.wait()  # hold both callers in-flight together
            return _df(["2026-06-01T14:30:00Z"])

    monkeypatch.setattr("app.config.get_provider", lambda name=None: Slow())

    t1 = asyncio.create_task(
        hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30))
    t2 = asyncio.create_task(
        hyd.get_chart_bars_hydrated("SIDU", interval="5m", lookback_days=30))
    await asyncio.sleep(0)
    gate.set()
    r1, r2 = await asyncio.gather(t1, t2)

    assert len(r1) == 1 and len(r2) == 1
    assert calls["n"] == 1  # collapsed to a single upstream fetch
