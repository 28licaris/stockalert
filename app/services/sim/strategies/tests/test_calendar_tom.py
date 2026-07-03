"""calendar_tom: enter near month-end, exit after N bars of the new month."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.backtester import Backtester
from app.services.sim.schemas import BacktestConfig
from app.services.sim.strategies.calendar_tom import CalendarTomParams, CalendarTomStrategy

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


def test_tom_window_entry_and_exit():
    bars = _weekday_bars(dt.date(2024, 1, 2), dt.date(2024, 4, 30))
    bt = Backtester()
    bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
    bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
    bt._fetch_bars_multi = lambda *a, **k: {"1d": {"SPY": bars}}  # type: ignore[assignment]
    cfg = BacktestConfig(
        symbols=["SPY"], start=bars[0].timestamp, end=bars[-1].timestamp,
        interval="1d", starting_cash=100_000.0, history_window=10, fees_model="zero",
    )
    strat = CalendarTomStrategy(CalendarTomParams(
        enter_calendar_days=8, exit_after_bars=2, position_size_pct=0.5))
    res = bt.run_portfolio(strat, cfg)

    buys = [t for t in res.trades if t.side == "buy"]
    sells = [t for t in res.trades if t.side == "sell"]
    assert len(buys) >= 3  # one entry per month boundary in the window
    for t in buys:
        d = t.timestamp
        days_in_month = (dt.date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
                         - dt.timedelta(days=1)).day
        # decision was <=8 calendar days before month-end; fill is next open,
        # so the fill lands in the final stretch of the month.
        assert days_in_month - d.day <= 8
    for t in sells:
        # exit decision at close of the 2nd trading day -> fill on the 3rd
        # trading day of the new month at the latest (weekends shift days).
        assert t.timestamp.day <= 7
    # Flat mid-month: no position on e.g. Feb 15
    mid_feb = [t for t in res.trades if t.timestamp.month == 2 and 8 <= t.timestamp.day <= 18]
    assert not mid_feb
