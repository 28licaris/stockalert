"""hourly_swing: trigger bars, entry fills, timer exits."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.services.sim.backtester import Backtester
from app.services.sim.schemas import BacktestConfig
from app.services.sim.strategies.hourly_swing import HourlySwingParams, HourlySwingStrategy

ET = ZoneInfo("America/New_York")


@dataclass
class _Bar:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1_000_000.0


def _bars(days_spec):
    """days_spec: list of (date, [7 x (o,h,l,c)])."""
    out = []
    for d, prices in days_spec:
        for h, (o, hi, lo, c) in enumerate(prices):
            out.append(_Bar("XYZ", dt.datetime(d.year, d.month, d.day, 9 + h, 30, tzinfo=ET),
                            o, hi, lo, c))
    return out


def _run(bars, trigger):
    bt = Backtester()
    bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
    bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
    bt._fetch_bars_multi = lambda *a, **k: {"1h": {"XYZ": bars}}  # type: ignore[assignment]
    cfg = BacktestConfig(symbols=["XYZ"], start=bars[0].timestamp, end=bars[-1].timestamp,
                         interval="1h", starting_cash=100_000.0, history_window=10,
                         fees_model="zero")
    strat = HourlySwingStrategy(HourlySwingParams(trigger=trigger, position_size_pct=0.5))
    return bt.run_portfolio(strat, cfg)


def test_gap_hold_entry_and_timer_exit():
    flat = [(100, 100.5, 99.5, 100)] * 7
    # Day 2 gaps +2% (open 102) and HOLDS above the open through bar 2.
    day2 = [(102, 102.5, 101.8, 102.2), (102.2, 102.6, 102.0, 102.4)] + [(102.4, 103, 102, 102.5)] * 5
    day3 = [(102.5, 103, 102, 102.6)] * 7
    bars = _bars([(dt.date(2024, 3, 11), flat), (dt.date(2024, 3, 12), day2),
                  (dt.date(2024, 3, 13), day3)])
    res = _run(bars, "gap_hold")
    buys = [t for t in res.trades if t.side == "buy"]
    sells = [t for t in res.trades if t.side == "sell"]
    # decision at day2 bar_idx==1 close (11:30) -> fill bar2 open (11:30)
    assert [(t.timestamp.day, t.timestamp.hour) for t in buys] == [(12, 11)]
    # timer: 7 bars held -> sell decision day3 bar 1 close -> fill day3 12:30... 
    assert len(sells) == 1 and sells[0].timestamp.day == 13


def test_first_hour_break_entry():
    day1 = [(100, 100.5, 99.5, 100)] * 7          # prior high 100.5
    day2 = [(100.4, 101.2, 100.3, 101.0)] + [(101, 101.5, 100.8, 101.2)] * 6  # bar0 close 101 > 100.5
    day3 = [(101.2, 101.6, 101.0, 101.3)] * 7
    bars = _bars([(dt.date(2024, 3, 11), day1), (dt.date(2024, 3, 12), day2),
                  (dt.date(2024, 3, 13), day3)])
    res = _run(bars, "first_hour_break")
    buys = [t for t in res.trades if t.side == "buy"]
    assert [(t.timestamp.day, t.timestamp.hour) for t in buys] == [(12, 10)]  # fill bar1 open


def test_no_trigger_no_trades():
    flat = [(100, 100.5, 99.5, 100)] * 7
    bars = _bars([(dt.date(2024, 3, 11), flat), (dt.date(2024, 3, 12), flat),
                  (dt.date(2024, 3, 13), flat)])
    for trig in ("gap_hold", "first_hour_break"):
        assert _run(bars, trig).trades == []
