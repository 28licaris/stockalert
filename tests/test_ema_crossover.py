"""
Unit tests for EmaCrossoverStrategy.

Pure strategy, mirrors the SMA Crossover test structure since
the logic is identical except for the MA family. Includes a
direct comparison test: EMA fires the cross strictly EARLIER
than SMA on the same crossing series (the property that
distinguishes them).
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
from app.services.sim.strategies.ema_crossover import (
    EmaCrossoverParams,
    EmaCrossoverStrategy,
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
            open_=c, high=c * 1.005, low=c * 0.995, close=c, volume=1000.0,
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


def _long_snapshot(qty: float, avg_price: float = 100.0) -> PortfolioSnapshot:
    pos = Position(
        symbol="TEST", quantity=qty, avg_entry_price=avg_price,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    return PortfolioSnapshot(cash=5_000.0, equity=5_000.0 + qty * avg_price, positions={"TEST": pos})


# Series that guarantees a cross-up: flat then rising sharply.
_CROSSING_CLOSES = [100.0] * 12 + list(range(105, 150, 3))


# ─────────────────────────────────────────────────────────────────────
# Param validation
# ─────────────────────────────────────────────────────────────────────


def test_params_reject_overlapping_periods() -> None:
    with pytest.raises(ValueError, match="must be <"):
        EmaCrossoverStrategy(params=EmaCrossoverParams(fast_period=50, slow_period=20))


def test_params_reject_equal_periods() -> None:
    with pytest.raises(ValueError, match="must be <"):
        EmaCrossoverStrategy(params=EmaCrossoverParams(fast_period=20, slow_period=20))


# ─────────────────────────────────────────────────────────────────────
# Warmup
# ─────────────────────────────────────────────────────────────────────


def test_holds_during_warmup() -> None:
    strat = EmaCrossoverStrategy(params=EmaCrossoverParams(fast_period=2, slow_period=4))
    ctx = Context(config=_config())
    for bar in _bars("TEST", [10.0, 11.0, 12.0]):  # need slow_period+1=5 bars
        ctx.advance(bar, _flat_snapshot())
        assert strat.on_bar(ctx).kind == "hold"


# ─────────────────────────────────────────────────────────────────────
# Cross signals
# ─────────────────────────────────────────────────────────────────────


def test_emits_buy_on_cross_up_when_flat() -> None:
    strat = EmaCrossoverStrategy(params=EmaCrossoverParams(
        fast_period=2, slow_period=4, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    actions: list[str] = []
    for bar in _bars("TEST", _CROSSING_CLOSES):
        ctx.advance(bar, _flat_snapshot())
        actions.append(strat.on_bar(ctx).kind)
    assert "buy" in actions


def test_does_not_buy_when_already_long() -> None:
    strat = EmaCrossoverStrategy(params=EmaCrossoverParams(fast_period=2, slow_period=4))
    ctx = Context(config=_config())
    snap = _long_snapshot(qty=50.0)
    for bar in _bars("TEST", _CROSSING_CLOSES):
        ctx.advance(bar, snap)
        assert strat.on_bar(ctx).kind != "buy"


def test_emits_sell_on_cross_down_when_long() -> None:
    """Reverse-crossing series + held position → SELL."""
    strat = EmaCrossoverStrategy(params=EmaCrossoverParams(fast_period=2, slow_period=4))
    ctx = Context(config=_config())
    snap = _long_snapshot(qty=50.0)
    # Rising then falling: should produce a cross-down somewhere.
    closes = [100.0] * 5 + list(range(110, 140, 3)) + list(range(135, 90, -3))
    actions: list[str] = []
    for bar in _bars("TEST", closes):
        ctx.advance(bar, snap)
        actions.append(strat.on_bar(ctx).kind)
    assert "sell" in actions


def test_does_not_sell_when_flat() -> None:
    strat = EmaCrossoverStrategy(params=EmaCrossoverParams(fast_period=2, slow_period=4))
    ctx = Context(config=_config())
    closes = list(range(140, 100, -2))
    for bar in _bars("TEST", closes):
        ctx.advance(bar, _flat_snapshot())
        assert strat.on_bar(ctx).kind != "sell"


# ─────────────────────────────────────────────────────────────────────
# Sizing
# ─────────────────────────────────────────────────────────────────────


def test_buy_size_is_integer_shares() -> None:
    strat = EmaCrossoverStrategy(params=EmaCrossoverParams(
        fast_period=2, slow_period=4, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    actions = []
    for bar in _bars("TEST", _CROSSING_CLOSES):
        ctx.advance(bar, _flat_snapshot())
        actions.append(strat.on_bar(ctx))
    buys = [a for a in actions if a.kind == "buy"]
    assert buys
    for buy in buys:
        assert buy.size == float(int(buy.size))
        assert buy.size > 0


# ─────────────────────────────────────────────────────────────────────
# EMA vs SMA — direct A/B
# ─────────────────────────────────────────────────────────────────────


def test_ema_fires_earlier_than_sma_on_same_cross() -> None:
    """
    On the same rising series, the EMA crossover should fire AT
    OR BEFORE the SMA crossover at the same fast/slow periods.
    This is the defining property of EMA (weights recent prices
    more, reacts faster).

    Run both strategies through the same bar stream + flat
    portfolio; assert the first 'buy' action index in the EMA
    history is <= the first 'buy' in the SMA history.
    """
    from app.services.sim.strategies.sma_crossover import (
        SmaCrossoverParams,
        SmaCrossoverStrategy,
    )

    closes = [100.0] * 15 + list(range(100, 140, 2))  # flat then sharp up

    def _first_buy_idx(strat) -> int:
        ctx = Context(config=_config())
        for i, bar in enumerate(_bars("TEST", closes)):
            ctx.advance(bar, _flat_snapshot())
            if strat.on_bar(ctx).kind == "buy":
                return i
        return -1

    ema = EmaCrossoverStrategy(params=EmaCrossoverParams(fast_period=3, slow_period=10))
    sma = SmaCrossoverStrategy(params=SmaCrossoverParams(fast_period=3, slow_period=10))

    ema_idx = _first_buy_idx(ema)
    sma_idx = _first_buy_idx(sma)

    assert ema_idx >= 0, "expected an EMA buy"
    assert sma_idx >= 0, "expected an SMA buy"
    assert ema_idx <= sma_idx, (
        f"EMA should fire at or before SMA on the same cross "
        f"(got EMA@{ema_idx}, SMA@{sma_idx})"
    )


# ─────────────────────────────────────────────────────────────────────
# Metadata + Protocol
# ─────────────────────────────────────────────────────────────────────


def test_strategy_metadata() -> None:
    strat = EmaCrossoverStrategy()
    assert strat.name == "ema_crossover"
    assert strat.version == "0.1"
    assert strat.interval == "1d"


def test_strategy_satisfies_strategy_protocol() -> None:
    from app.services.sim.strategy import Strategy
    assert isinstance(EmaCrossoverStrategy(), Strategy)
