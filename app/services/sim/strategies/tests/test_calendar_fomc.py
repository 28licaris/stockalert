"""calendar_fomc: fill lands pre_weekdays before the announcement; exit at its open."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.backtester import Backtester
from app.services.sim.schemas import BacktestConfig
from app.services.sim.strategies.calendar_fomc import CalendarFomcParams, CalendarFomcStrategy

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


def _weekday_bars(start: dt.date, end: dt.date) -> list[_Bar]:
    bars, d, px = [], start, 100.0
    while d <= end:
        if d.weekday() < 5:
            bars.append(_Bar("SPY", dt.datetime(d.year, d.month, d.day, tzinfo=UTC),
                             px, px + 0.5, px - 0.5, px))
            px += 0.1
        d += dt.timedelta(days=1)
    return bars


def test_fomc_window_k1():
    # Announcement Wed 2024-03-20: k=1 -> entry fill Tue 03-19, exit fill Wed 03-20.
    bars = _weekday_bars(dt.date(2024, 3, 1), dt.date(2024, 3, 29))
    bt = Backtester()
    bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
    bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
    bt._fetch_bars_multi = lambda *a, **k: {"1d": {"SPY": bars}}  # type: ignore[assignment]
    cfg = BacktestConfig(
        symbols=["SPY"], start=bars[0].timestamp, end=bars[-1].timestamp,
        interval="1d", starting_cash=100_000.0, history_window=10, fees_model="zero",
    )
    strat = CalendarFomcStrategy(CalendarFomcParams(
        announcement_dates=["2024-03-20"], pre_weekdays=1, position_size_pct=0.5))
    res = bt.run_portfolio(strat, cfg)
    buys = [t for t in res.trades if t.side == "buy"]
    sells = [t for t in res.trades if t.side == "sell"]
    assert [t.timestamp.date() for t in buys] == [dt.date(2024, 3, 19)]
    assert [t.timestamp.date() for t in sells] == [dt.date(2024, 3, 20)]
