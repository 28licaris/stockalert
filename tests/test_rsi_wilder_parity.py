"""
EXP-40 parity: the engine's rsi_reversion(wilder) must be the SAME rule
the Tier-1 MCPT screen validated (scripts/mcpt_insample meanrev_rsi) —
same RSI math, same entry/exit decision bars. If this test fails, Tier-2
is testing a different (unvalidated) strategy.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.indicators.rsi_wilder import RSIWilder
from app.services.sim.backtester import Backtester
from app.services.sim.schemas import BacktestConfig
from app.services.sim.strategies.rsi_reversion import RsiReversionParams, RsiReversionStrategy
from scripts.mcpt_insample import _meanrev_rsi, _wilder_rsi_wide

UTC = dt.timezone.utc
T0 = dt.datetime(2022, 1, 3, tzinfo=UTC)


def _series(n=400, seed=7):
    rng = np.random.default_rng(seed)
    rets = 0.0002 + 0.02 * rng.standard_normal(n)
    rets[80:88] -= 0.03   # engineered capitulation episodes
    rets[240:246] -= 0.04
    return pd.Series(100.0 * np.exp(np.cumsum(rets)))


def test_indicator_matches_screen_math():
    close = _series()
    wide = pd.DataFrame({"A": close})
    screen = _wilder_rsi_wide(wide, 4)["A"]
    engine = RSIWilder(period=4).compute(close)
    pd.testing.assert_series_equal(engine, screen, check_names=False)


@dataclass
class _Bar:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1_000_000.0


def test_strategy_decision_bars_match_screen_state_machine():
    close = _series()
    wide = {"close": pd.DataFrame({"A": close}), "volume": pd.DataFrame({"A": close * 0})}
    sig = _meanrev_rsi(wide, n=4, entry=10, exit=50)["A"].to_numpy()
    d = np.diff(np.concatenate([[0.0], sig]))
    screen_entries = set(np.flatnonzero(d == 1).tolist())
    screen_exits = set(np.flatnonzero(d == -1).tolist())
    assert screen_entries, "fixture produced no oversold episodes — rebuild it"

    bars = [
        _Bar("A", T0 + dt.timedelta(days=i), c, c * 1.005, c * 0.995, c)
        for i, c in enumerate(close)
    ]
    ts_to_idx = {b.timestamp: i for i, b in enumerate(bars)}
    bt = Backtester()
    bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
    bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
    bt._fetch_bars_multi = lambda *a, **k: {"1d": {"A": bars}}  # type: ignore[assignment]
    cfg = BacktestConfig(
        symbols=["A"], start=T0, end=T0 + dt.timedelta(days=500), interval="1d",
        starting_cash=100_000.0, history_window=450, fees_model="zero",
    )
    strat = RsiReversionStrategy(
        params=RsiReversionParams(
            rsi_period=4, oversold_threshold=10, exit_threshold=50,
            position_size_pct=0.5, rsi_kind="wilder"),
        interval="1d",
    )
    res = bt.run_portfolio(strat, cfg)

    # Fill is at bar t+1's open for a decision made on bar t's close, so the
    # decision bar = fill bar - 1. Those must equal the screen's transitions.
    engine_entries = {ts_to_idx[t.timestamp] - 1 for t in res.trades if t.side == "buy"}
    engine_exits = {ts_to_idx[t.timestamp] - 1 for t in res.trades if t.side == "sell"}
    assert engine_entries == screen_entries
    # The final position may still be open at end-of-data — every engine exit
    # must be a screen exit, and any missing screen exit must be the last one.
    assert engine_exits <= screen_exits
    missing = screen_exits - engine_exits
    assert len(missing) <= 1 and all(m > max(engine_entries) for m in missing)
