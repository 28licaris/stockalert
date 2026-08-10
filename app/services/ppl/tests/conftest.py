"""Synthetic bar fixtures — the whole PPL pipeline is testable without IO."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

ET = ZoneInfo("America/New_York")

# 09:30-anchored 30-minute session, matching the ohlcv_hourly convention's
# sibling: 13 bars from 09:30 to 15:30 ET.
BARS_PER_SESSION_30M = 13


@dataclass(frozen=True)
class FakeBar:
    """Minimal stand-in for `readers.schemas.LiveBar` (duck-typed by PplService)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1_000.0


def session_timestamps(n_sessions: int, *, interval_minutes: int = 30,
                       start_date: str = "2026-01-05") -> list[datetime]:
    """Weekday sessions of 09:30-anchored ET bars, returned as UTC instants."""
    bars_per_session = int(6.5 * 60 / interval_minutes)
    day = datetime.fromisoformat(start_date).date()
    out: list[datetime] = []
    while len(out) < n_sessions * bars_per_session:
        if day.weekday() < 5:                     # Mon-Fri; holidays are irrelevant here
            open_et = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(
                hour=9, minute=30
            )
            for i in range(bars_per_session):
                out.append(
                    (open_et + timedelta(minutes=interval_minutes * i)).astimezone(
                        timezone.utc
                    )
                )
        day += timedelta(days=1)
    return out


def make_bars(
    n_sessions: int = 80,
    *,
    interval_minutes: int = 30,
    sigma: float = 0.004,
    drift: float = 0.0,
    start_price: float = 100.0,
    seed: int = 42,
) -> list[FakeBar]:
    """A geometric random walk laid out on real session timestamps."""
    ts = session_timestamps(n_sessions, interval_minutes=interval_minutes)
    rng = np.random.default_rng(seed)
    closes = start_price * np.exp(np.cumsum(rng.normal(drift, sigma, len(ts))))

    bars: list[FakeBar] = []
    prev = start_price
    for t, c in zip(ts, closes):
        hi = max(prev, c) * (1 + abs(rng.normal(0, sigma / 3)))
        lo = min(prev, c) * (1 - abs(rng.normal(0, sigma / 3)))
        bars.append(FakeBar(timestamp=t, open=prev, high=hi, low=lo, close=float(c)))
        prev = float(c)
    return bars


def bars_to_frame(bars: list[FakeBar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    )


@pytest.fixture
def bars() -> list[FakeBar]:
    return make_bars()


@pytest.fixture
def frame(bars) -> pd.DataFrame:
    return bars_to_frame(bars)


@pytest.fixture
def bars_fn(bars):
    """Drop-in for `bars_gateway.get_chart_bars`."""

    def _fetch(symbol: str, *, interval: str = "30m", lookback_days=None, **_):
        return bars

    return _fetch
