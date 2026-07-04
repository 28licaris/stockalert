"""calendar_fomc_hourly: enter prior-day 15:30 fill, exit 13:30 fill on decision day."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.backtester import Backtester
from app.services.sim.schemas import BacktestConfig
from app.services.sim.strategies.calendar_fomc_hourly import (
    CalendarFomcHourlyParams,
    CalendarFomcHourlyStrategy,
)

UTC = dt.timezone.utc


@dataclass
class _Bar:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1_000_000.0


def _session_bars(start: dt.date, end: dt.date) -> list[_Bar]:
    bars, d, px = [], start, 100.0
    while d <= end:
        if d.weekday() < 5:
            for h in range(7):  # 09:30..15:30 starts
                t = dt.datetime(d.year, d.month, d.day, 9 + h, 30, tzinfo=UTC)
                bars.append(_Bar("SPY", t, px, px + 0.2, px - 0.2, px))
                px += 0.01
        d += dt.timedelta(days=1)
    return bars


def test_hourly_fomc_window():
    # Announcement Wed 2024-03-20 -> enter fill Tue 15:30, exit fill Wed 13:30.
    bars = _session_bars(dt.date(2024, 3, 11), dt.date(2024, 3, 26))
    bt = Backtester()
    bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
    bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
    bt._fetch_bars_multi = lambda *a, **k: {"1h": {"SPY": bars}}  # type: ignore[assignment]
    cfg = BacktestConfig(
        symbols=["SPY"], start=bars[0].timestamp, end=bars[-1].timestamp,
        interval="1h", starting_cash=100_000.0, history_window=10, fees_model="zero",
    )
    strat = CalendarFomcHourlyStrategy(CalendarFomcHourlyParams(
        announcement_dates=["2024-03-20"], position_size_pct=0.5))
    res = bt.run_portfolio(strat, cfg)
    buys = [t for t in res.trades if t.side == "buy"]
    sells = [t for t in res.trades if t.side == "sell"]
    assert [(t.timestamp.date(), t.timestamp.strftime("%H:%M")) for t in buys] == [
        (dt.date(2024, 3, 19), "15:30")]
    assert [(t.timestamp.date(), t.timestamp.strftime("%H:%M")) for t in sells] == [
        (dt.date(2024, 3, 20), "13:30")]
