"""Portfolio backtest (multi-symbol, shared equity) + RiskManager tests."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.backtester import Backtester
from app.services.sim.risk import RiskManager
from app.services.sim.schemas import Action, BacktestConfig, hold
from app.services.sim.strategy import BaseStrategy

UTC = dt.timezone.utc
T0 = dt.datetime(2024, 1, 1, tzinfo=UTC)


@dataclass
class _Bar:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1_000_000.0


def _bars(symbol, n=6, base=100.0):
    return [_Bar(symbol, T0 + dt.timedelta(days=i), base + i, base + i + 0.5,
                 base + i - 0.5, base + i) for i in range(n)]


class _RoundTrip(BaseStrategy):
    """Buy 10 when flat on the 2nd bar seen; sell on the 4th. Deterministic."""
    name = "rt"; version = "0"; interval = "1d"

    def on_bar(self, ctx):
        sym = ctx.bar.symbol
        pos = ctx.portfolio.positions.get(sym)
        has = pos is not None and pos.quantity > 0
        n = len(ctx.history)
        if not has and n == 2:
            return Action(kind="buy", symbol=sym, size=10, stop_price=ctx.bar.close * 0.9)
        if has and n == 4:
            return Action(kind="sell", symbol=sym, size=10)
        return hold()


def _cfg(symbols, **kw):
    return BacktestConfig(symbols=symbols, start=T0, end=T0 + dt.timedelta(days=10),
                          interval="1d", starting_cash=100_000.0, history_window=50, **kw)


def _patch(bt, monkeypatch, bars_by_symbol):
    monkeypatch.setattr(bt, "_capture_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(bt, "_load_benchmark", lambda *a, **k: None)
    monkeypatch.setattr(bt, "_fetch_bars_multi", lambda *a, **k: {"1d": bars_by_symbol})


# ── RiskManager ──────────────────────────────────────────────────────

def test_risk_manager_concurrent_cap() -> None:
    rm = RiskManager(max_concurrent=2, max_portfolio_heat=1.0)
    rm.register("A", 100); rm.register("B", 100)
    assert not rm.can_open("C", 100, equity=100_000)   # 3rd exceeds max_concurrent
    rm.release("A")
    assert rm.can_open("C", 100, equity=100_000)


def test_risk_manager_heat_cap() -> None:
    rm = RiskManager(max_concurrent=10, max_portfolio_heat=0.02)  # 2% of equity
    rm.register("A", 1500)
    # equity 100k → cap $2000. 1500 + 600 > 2000 → blocked; 1500 + 400 ok.
    assert not rm.can_open("B", 600, equity=100_000)
    assert rm.can_open("B", 400, equity=100_000)


# ── portfolio loop ───────────────────────────────────────────────────

def test_portfolio_runs_two_symbols_concurrently(monkeypatch) -> None:
    bt = Backtester()
    _patch(bt, monkeypatch, {"A": _bars("A"), "B": _bars("B", base=200.0)})
    res = bt.run_portfolio(_RoundTrip(), _cfg(["A", "B"]))
    # One shared equity curve (one point per timeline timestamp = 6 days).
    assert len(res.equity_curve) == 6
    # Both symbols round-tripped: 2 entries + 2 closing legs.
    assert sum(1 for t in res.trades if t.is_closing) == 2


def test_portfolio_risk_cap_blocks_second_entry(monkeypatch) -> None:
    bt = Backtester()
    _patch(bt, monkeypatch, {"A": _bars("A"), "B": _bars("B", base=200.0)})
    # max_concurrent=1 → A enters first (processed first), B blocked that bar.
    res = bt.run_portfolio(_RoundTrip(), _cfg(["A", "B"], max_concurrent_positions=1))
    # Only one symbol ever opened → exactly one closing leg.
    assert sum(1 for t in res.trades if t.is_closing) == 1
