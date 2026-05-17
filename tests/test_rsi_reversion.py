"""
Unit tests for RsiReversionStrategy.

Strategy is pure (per the modularity contract), so tests use
synthetic bar streams + injected portfolio snapshots. No CH /
no providers needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.sim.context import Context
from app.services.sim.schemas import (
    BacktestConfig,
    PortfolioSnapshot,
    Position,
)
from app.services.sim.strategies.rsi_reversion import (
    RsiReversionParams,
    RsiReversionStrategy,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


class _SyntheticBar:
    def __init__(self, symbol, ts, open_, high, low, close, volume=1000.0):
        self.symbol = symbol
        self.timestamp = ts
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def _bars(symbol: str, closes: list[float]) -> list[_SyntheticBar]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        _SyntheticBar(
            symbol=symbol, ts=base + timedelta(days=i),
            open_=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbols=["TEST"],
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        interval="1d",
        starting_cash=10_000.0,
        history_window=200,
    )


def _flat_snapshot(cash: float = 10_000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=cash, equity=cash)


def _long_snapshot(qty: float, avg_price: float = 100.0, cash: float = 5_000.0) -> PortfolioSnapshot:
    pos = Position(
        symbol="TEST", quantity=qty, avg_entry_price=avg_price,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    return PortfolioSnapshot(cash=cash, equity=cash + qty * avg_price, positions={"TEST": pos})


# Crafted series that goes flat → drops sharply → recovers. This
# produces RSI(14) values that dip BELOW 30 then rise ABOVE 50 — the
# canonical mean-revert opportunity. We use this for both the entry
# and exit tests; the strategy state-transitions naturally.
_CRAFTED_DIP_CLOSES = (
    [100.0] * 15           # flat baseline (RSI converges to 50)
    + [99.0, 97.0, 94.0, 90.0, 85.0, 80.0, 78.0]  # sharp drop -> RSI dives below 30
    + [82.0, 86.0, 90.0, 94.0, 98.0, 102.0]       # recovery -> RSI back through 50
)


# ─────────────────────────────────────────────────────────────────────
# Param validation
# ─────────────────────────────────────────────────────────────────────


def test_params_reject_overlapping_thresholds() -> None:
    """oversold >= exit_threshold is degenerate (would buy and sell same bar)."""
    with pytest.raises(ValueError, match="must be <"):
        RsiReversionStrategy(params=RsiReversionParams(
            oversold_threshold=50.0, exit_threshold=30.0,
        ))


def test_params_reject_oversold_equals_exit() -> None:
    with pytest.raises(ValueError, match="must be <"):
        RsiReversionStrategy(params=RsiReversionParams(
            oversold_threshold=50.0, exit_threshold=50.0,
        ))


# ─────────────────────────────────────────────────────────────────────
# Warmup
# ─────────────────────────────────────────────────────────────────────


def test_holds_during_warmup() -> None:
    """RSI(14) needs at least 16 bars in history to produce a value."""
    strat = RsiReversionStrategy(params=RsiReversionParams(rsi_period=14))
    ctx = Context(config=_config())
    for bar in _bars("TEST", [100.0, 101.0, 102.0, 103.0, 104.0]):  # 5 bars
        ctx.advance(bar, _flat_snapshot())
        action = strat.on_bar(ctx)
        assert action.kind == "hold"


# ─────────────────────────────────────────────────────────────────────
# Entry signal
# ─────────────────────────────────────────────────────────────────────


def test_emits_buy_on_rsi_dip_below_oversold() -> None:
    """Sharp drop drives RSI under 30 → strategy buys when flat."""
    strat = RsiReversionStrategy(params=RsiReversionParams(
        rsi_period=14, oversold_threshold=30.0, exit_threshold=50.0,
        position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    actions: list[str] = []
    for bar in _bars("TEST", _CRAFTED_DIP_CLOSES):
        ctx.advance(bar, _flat_snapshot())
        actions.append(strat.on_bar(ctx).kind)
    assert "buy" in actions, f"expected a buy somewhere; got {actions}"
    # The buy should happen DURING the dip, not on the recovery.
    first_buy_idx = actions.index("buy")
    assert first_buy_idx < len(actions) - 2  # not at the very end


def test_does_not_buy_when_already_long() -> None:
    """RSI under oversold + we already hold a position → hold, not double-up."""
    strat = RsiReversionStrategy(params=RsiReversionParams(rsi_period=14))
    ctx = Context(config=_config())
    snap = _long_snapshot(qty=50.0, avg_price=100.0)
    actions: list[str] = []
    for bar in _bars("TEST", _CRAFTED_DIP_CLOSES):
        ctx.advance(bar, snap)
        actions.append(strat.on_bar(ctx).kind)
    # Never a buy — we were already long throughout.
    assert "buy" not in actions


def test_does_not_buy_when_rsi_above_oversold() -> None:
    """RSI stays above 30 throughout the run → no buy."""
    strat = RsiReversionStrategy(params=RsiReversionParams(
        rsi_period=14, oversold_threshold=30.0,
    ))
    ctx = Context(config=_config())
    # Steadily rising series → RSI well above 50.
    actions: list[str] = []
    for bar in _bars("TEST", [100.0 + i for i in range(30)]):
        ctx.advance(bar, _flat_snapshot())
        actions.append(strat.on_bar(ctx).kind)
    assert "buy" not in actions


# ─────────────────────────────────────────────────────────────────────
# Exit signal
# ─────────────────────────────────────────────────────────────────────


def test_emits_sell_on_recovery_above_exit() -> None:
    """RSI recovers above 50 while we hold → strategy sells."""
    strat = RsiReversionStrategy(params=RsiReversionParams(
        rsi_period=14, oversold_threshold=30.0, exit_threshold=50.0,
    ))
    ctx = Context(config=_config())
    snap = _long_snapshot(qty=50.0, avg_price=100.0)
    actions: list[str] = []
    for bar in _bars("TEST", _CRAFTED_DIP_CLOSES):
        ctx.advance(bar, snap)
        actions.append(strat.on_bar(ctx).kind)
    assert "sell" in actions


def test_does_not_sell_when_flat() -> None:
    """RSI above exit_threshold but no position → hold (nothing to sell)."""
    strat = RsiReversionStrategy(params=RsiReversionParams(rsi_period=14))
    ctx = Context(config=_config())
    actions: list[str] = []
    for bar in _bars("TEST", [100.0 + i for i in range(30)]):
        ctx.advance(bar, _flat_snapshot())
        actions.append(strat.on_bar(ctx).kind)
    assert "sell" not in actions


# ─────────────────────────────────────────────────────────────────────
# Sizing
# ─────────────────────────────────────────────────────────────────────


def test_buy_size_is_integer_shares() -> None:
    """Strategy floors qty to whole shares (`cash * pct / price`)."""
    strat = RsiReversionStrategy(params=RsiReversionParams(
        rsi_period=14, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    actions = []
    for bar in _bars("TEST", _CRAFTED_DIP_CLOSES):
        ctx.advance(bar, _flat_snapshot(cash=10_000.0))
        actions.append(strat.on_bar(ctx))
    buys = [a for a in actions if a.kind == "buy"]
    assert buys
    # All buy sizes must be integers (math.floor in the strategy).
    for buy in buys:
        assert buy.size == float(int(buy.size))
        assert buy.size > 0


def test_buy_size_zero_when_cash_insufficient() -> None:
    """If price > cash * pct, floor(qty) = 0 → hold."""
    strat = RsiReversionStrategy(params=RsiReversionParams(
        rsi_period=14, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    # Very high-priced bars vs tiny cash.
    for bar in _bars("TEST", [c * 10.0 for c in _CRAFTED_DIP_CLOSES]):
        ctx.advance(bar, _flat_snapshot(cash=50.0))
        action = strat.on_bar(ctx)
        assert action.kind == "hold"  # never enough cash to buy 1 share


# ─────────────────────────────────────────────────────────────────────
# Sanity: strategy declares the right interval + version
# ─────────────────────────────────────────────────────────────────────


def test_strategy_metadata() -> None:
    strat = RsiReversionStrategy()
    assert strat.name == "rsi_reversion"
    assert strat.version == "0.1"
    assert strat.interval == "1d"  # default

    strat_1h = RsiReversionStrategy(interval="1h")
    assert strat_1h.interval == "1h"


def test_strategy_satisfies_strategy_protocol() -> None:
    """RsiReversionStrategy duck-types into the Strategy Protocol."""
    from app.services.sim.strategy import Strategy
    strat = RsiReversionStrategy()
    assert isinstance(strat, Strategy)
