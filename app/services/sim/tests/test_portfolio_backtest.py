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


def _falling(symbol, n=10, base=200.0):
    return [_Bar(symbol, T0 + dt.timedelta(days=i), base - i, base - i + 0.5,
                 base - i - 0.5, base - i) for i in range(n)]


class _BuyAtN(BaseStrategy):
    """Buy 10 when flat at the buy_n-th bar; sell two bars later."""
    name = "bn"; version = "0"; interval = "1d"

    def __init__(self, buy_n):
        self.buy_n = buy_n

    def on_bar(self, ctx):
        sym = ctx.bar.symbol
        pos = ctx.portfolio.positions.get(sym)
        has = pos is not None and pos.quantity > 0
        n = len(ctx.history)
        if not has and n == self.buy_n:
            return Action(kind="buy", symbol=sym, size=10, stop_price=ctx.bar.close * 0.9)
        if has and n == self.buy_n + 2:
            return Action(kind="sell", symbol=sym, size=10)
        return hold()


def test_dynamic_universe_gates_long_to_momentum_leaders(monkeypatch) -> None:
    bt = Backtester()
    _patch(bt, monkeypatch, {"A": _bars("A", n=10), "B": _falling("B", n=10)})
    # top-1 leader: A rising (leader), B falling (laggard) → only A may go long.
    # Buy at bar 5 so the lookback-2 momentum is available when the gate evaluates.
    res = bt.run_portfolio(_BuyAtN(5), _cfg(["A", "B"], momentum_top_n=1, momentum_lookback=2))
    syms = {t.symbol for t in res.trades}
    assert "A" in syms and "B" not in syms
    assert sum(1 for t in res.trades if t.is_closing) == 1   # only the leader round-tripped


class _ConfEntry(BaseStrategy):
    """Enter 10 shares when flat on the 2nd bar, with per-symbol confidence."""
    name = "conf"; version = "0"; interval = "1d"

    def __init__(self, conf_by_symbol):
        self.conf_by_symbol = conf_by_symbol

    def on_bar(self, ctx):
        sym = ctx.bar.symbol
        pos = ctx.portfolio.positions.get(sym)
        has = pos is not None and pos.quantity > 0
        if not has and len(ctx.history) == 2:
            return Action(kind="buy", symbol=sym, size=10, stop_price=ctx.bar.close * 0.9,
                          confidence=self.conf_by_symbol[sym])
        return hold()


def test_first_come_admission_spends_slot_in_symbol_order(monkeypatch) -> None:
    # Legacy behavior (flag off): A wins the single slot despite lower confidence.
    bt = Backtester()
    _patch(bt, monkeypatch, {"A": _bars("A"), "B": _bars("B", base=200.0)})
    res = bt.run_portfolio(_ConfEntry({"A": 0.1, "B": 0.9}),
                           _cfg(["A", "B"], max_concurrent_positions=1))
    assert {t.symbol for t in res.trades} == {"A"}


def test_ranked_admission_spends_slot_on_highest_confidence(monkeypatch) -> None:
    # ranked_admission: the SAME same-bar competition goes to B (conf 0.9 > 0.1).
    bt = Backtester()
    _patch(bt, monkeypatch, {"A": _bars("A"), "B": _bars("B", base=200.0)})
    res = bt.run_portfolio(_ConfEntry({"A": 0.1, "B": 0.9}),
                           _cfg(["A", "B"], max_concurrent_positions=1,
                                ranked_admission=True))
    assert {t.symbol for t in res.trades} == {"B"}


class _Churn(BaseStrategy):
    """Buy `size` when flat; close the position on the next bar. On a falling
    tape every round trip realizes a loss, so equity drawdown accumulates."""
    name = "churn"; version = "0"; interval = "1d"

    def __init__(self, size=400):
        self.size = size

    def on_bar(self, ctx):
        sym = ctx.bar.symbol
        pos = ctx.portfolio.positions.get(sym)
        if pos is not None and pos.quantity > 0:
            return Action(kind="sell", symbol=sym, size=pos.quantity)
        if len(ctx.history) >= 2:
            return Action(kind="buy", symbol=sym, size=self.size,
                          stop_price=ctx.bar.close * 0.9, confidence=0.5)
        return hold()


def test_dd_brake_caps_loss_on_falling_tape(monkeypatch) -> None:
    # Churning a falling tape: without the governor losses compound well past
    # the 4% limit; with it, sizes shrink → total drawdown stays near the cap.
    bt = Backtester()
    _patch(bt, monkeypatch, {"X": _falling("X", n=40, base=200.0)})
    braked = bt.run_portfolio(_Churn(), _cfg(["X"], dd_brake_limit=0.04))
    bt2 = Backtester()
    _patch(bt2, monkeypatch, {"X": _falling("X", n=40, base=200.0)})
    free = bt2.run_portfolio(_Churn(), _cfg(["X"]))
    assert free.metrics.max_drawdown < -0.05            # unbraked loses > 5%
    assert braked.metrics.max_drawdown > free.metrics.max_drawdown
    assert braked.metrics.max_drawdown > -0.05          # capped near the 4% limit
    assert braked.metrics.final_equity > free.metrics.final_equity


def test_dd_brake_floor_keeps_participation(monkeypatch) -> None:
    # With floor=0.5, admitted entries below the limit are never under half size.
    bt = Backtester()
    _patch(bt, monkeypatch, {"X": _falling("X", n=20, base=200.0)})
    res = bt.run_portfolio(_Churn(),
                           _cfg(["X"], dd_brake_limit=0.30, dd_brake_floor=0.5))
    entries = [t for t in res.trades if not t.is_closing]
    assert entries and all(t.quantity >= 200 for t in entries)


def test_daily_table_bars_are_tz_aware(monkeypatch) -> None:
    # clickhouse-connect returns naive datetimes; the daily_table fetch must
    # coerce to aware UTC (Bar contract parity — paper compares curve ts
    # against an aware go_live).
    import app.db.client as dbc

    naive = dt.datetime(2024, 5, 14, 14, 30)
    fake_cli = type("C", (), {})()
    fake_cli.query = lambda *a, **k: type(
        "R", (), {"result_rows": [("AAPL", naive, 1.0, 2.0, 0.5, 1.5, 1e6)]})()
    monkeypatch.setattr(dbc, "get_client", lambda: fake_cli)
    bt = Backtester()
    out = bt._fetch_bars_daily_table(
        _cfg(["AAPL"], daily_table="ohlcv_daily"), "ohlcv_daily")
    (bar,) = out["AAPL"]
    assert bar.timestamp.tzinfo is not None
    assert bar.timestamp == naive.replace(tzinfo=dt.timezone.utc)


def test_dd_brake_empty_book_does_not_freeze(monkeypatch) -> None:
    # Fall past the limit (churn goes flat → without the liveness trickle the
    # governor deadlocks: frozen equity, zero entries forever), then rise —
    # trickle entries must keep the system alive and recover equity.
    fall = _falling("X", n=25, base=200.0)                      # 200 → 176
    rise = [_Bar("X", T0 + dt.timedelta(days=25 + i),
                 176 + 2 * i, 177 + 2 * i, 175 + 2 * i, 176 + 2 * i)
            for i in range(25)]
    bt = Backtester()
    _patch(bt, monkeypatch, {"X": fall + rise})
    res = bt.run_portfolio(_Churn(), _cfg(["X"], dd_brake_limit=0.02))
    cutoff = T0 + dt.timedelta(days=26)
    late_entries = [t for t in res.trades
                    if not t.is_closing and t.timestamp >= cutoff]
    assert late_entries, "governor deadlocked: no entries after the dd-limit hit"
    # the governor rode the recovery: equity climbed back off the trough
    trough = min(eq for _, eq in res.equity_curve)
    assert res.metrics.final_equity > trough
