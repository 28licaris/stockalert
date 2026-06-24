"""
Unit tests for BollingerMeanRevertStrategy.

Pure strategy, so tests use synthetic bar streams + injected
portfolio snapshots. Covers entry, exit, no-double-up, no-sell-flat,
warmup, sizing, and Strategy Protocol satisfaction.
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
from app.services.sim.strategies.bollinger_mean_revert import (
    BollingerMeanRevertParams,
    BollingerMeanRevertStrategy,
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


# Crafted close series:
#   - 20 bars of low-volatility flat → bands tight around 100
#   - 1 sharp dip to ~90 (≫ 2 std below the recent mean) → lower-band touch
#   - Several bars of recovery back to 100 → close >= middle band
_CRAFTED_BAND_TOUCH_CLOSES = (
    [100.0, 100.5, 100.0, 99.5, 100.0, 100.5, 100.0, 99.5,
     100.0, 100.5, 100.0, 99.5, 100.0, 100.5, 100.0, 99.5,
     100.0, 100.5, 100.0, 99.5]
    + [90.0]                                  # sharp dip — lower-band touch
    + [92.0, 95.0, 98.0, 100.0, 101.0, 102.0]  # recovery back to and above mean
)


# ─────────────────────────────────────────────────────────────────────
# Warmup
# ─────────────────────────────────────────────────────────────────────


def test_holds_during_warmup() -> None:
    """First (period + 1) bars → hold."""
    strat = BollingerMeanRevertStrategy(params=BollingerMeanRevertParams(period=20))
    ctx = Context(config=_config())
    for bar in _bars("TEST", [100.0] * 10):
        ctx.advance(bar, _flat_snapshot())
        assert strat.on_bar(ctx).kind == "hold"


# ─────────────────────────────────────────────────────────────────────
# Entry signal
# ─────────────────────────────────────────────────────────────────────


def test_emits_buy_on_lower_band_touch() -> None:
    """Close <= lower band + flat → BUY."""
    strat = BollingerMeanRevertStrategy(params=BollingerMeanRevertParams(
        period=20, std_multiplier=2.0, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    actions: list[str] = []
    for bar in _bars("TEST", _CRAFTED_BAND_TOUCH_CLOSES):
        ctx.advance(bar, _flat_snapshot())
        actions.append(strat.on_bar(ctx).kind)
    assert "buy" in actions
    # The buy fires AT the dip (bar 20) or shortly after, not during flat.
    first_buy = actions.index("buy")
    assert first_buy >= 20


def test_does_not_buy_when_already_long() -> None:
    strat = BollingerMeanRevertStrategy()
    ctx = Context(config=_config())
    snap = _long_snapshot(qty=50.0, avg_price=100.0)
    for bar in _bars("TEST", _CRAFTED_BAND_TOUCH_CLOSES):
        ctx.advance(bar, snap)
        action = strat.on_bar(ctx)
        assert action.kind != "buy", "should never double up"


def test_does_not_buy_when_close_above_lower_band() -> None:
    """Steady flat series — close stays at midline, never touches lower band."""
    strat = BollingerMeanRevertStrategy(params=BollingerMeanRevertParams(period=10))
    ctx = Context(config=_config())
    actions: list[str] = []
    # Tiny noise around 100, never enough to cross lower band by 2 std.
    closes = [100.0, 100.05, 99.95, 100.02, 99.98, 100.01, 99.99,
              100.03, 99.97, 100.0, 100.0, 99.98, 100.02, 100.0, 100.0]
    for bar in _bars("TEST", closes):
        ctx.advance(bar, _flat_snapshot())
        actions.append(strat.on_bar(ctx).kind)
    assert "buy" not in actions


# ─────────────────────────────────────────────────────────────────────
# Exit signal
# ─────────────────────────────────────────────────────────────────────


def test_emits_sell_on_middle_band_recovery() -> None:
    """Close >= middle (SMA) while long → SELL full position."""
    strat = BollingerMeanRevertStrategy()
    ctx = Context(config=_config())
    snap = _long_snapshot(qty=50.0, avg_price=90.0)
    actions: list[str] = []
    for bar in _bars("TEST", _CRAFTED_BAND_TOUCH_CLOSES):
        ctx.advance(bar, snap)
        actions.append(strat.on_bar(ctx).kind)
    assert "sell" in actions


def test_does_not_sell_when_flat() -> None:
    strat = BollingerMeanRevertStrategy()
    ctx = Context(config=_config())
    for bar in _bars("TEST", [100.0 + i * 0.1 for i in range(30)]):
        ctx.advance(bar, _flat_snapshot())
        action = strat.on_bar(ctx)
        assert action.kind != "sell"


# ─────────────────────────────────────────────────────────────────────
# Sizing
# ─────────────────────────────────────────────────────────────────────


def test_buy_size_is_integer_shares() -> None:
    strat = BollingerMeanRevertStrategy(params=BollingerMeanRevertParams(
        period=20, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    actions = []
    for bar in _bars("TEST", _CRAFTED_BAND_TOUCH_CLOSES):
        ctx.advance(bar, _flat_snapshot(cash=10_000.0))
        actions.append(strat.on_bar(ctx))
    buys = [a for a in actions if a.kind == "buy"]
    assert buys
    for buy in buys:
        assert buy.size == float(int(buy.size))
        assert buy.size > 0


def test_buy_size_zero_when_cash_insufficient() -> None:
    """Price > cash * pct → hold (no zero-size buy)."""
    strat = BollingerMeanRevertStrategy(params=BollingerMeanRevertParams(
        period=20, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    # Inflate prices so a $50 budget can't afford even one share.
    for bar in _bars("TEST", [c * 10.0 for c in _CRAFTED_BAND_TOUCH_CLOSES]):
        ctx.advance(bar, _flat_snapshot(cash=50.0))
        action = strat.on_bar(ctx)
        assert action.kind == "hold"


# ─────────────────────────────────────────────────────────────────────
# Bands math sanity — IndicatorReader equivalence
# ─────────────────────────────────────────────────────────────────────


def test_strategy_bands_match_bollinger_indicator() -> None:
    """
    Strategy's internal _bands() must produce the same numbers
    that `BollingerBands.compute_full` does. This is the
    "single source of truth" invariant — if these diverge, the
    strategy and the dashboard are reading different bands for
    the same window.
    """
    from app.indicators.bollinger import BollingerBands

    strat = BollingerMeanRevertStrategy(params=BollingerMeanRevertParams(
        period=10, std_multiplier=2.0,
    ))
    ctx = Context(config=_config())
    for bar in _bars("TEST", _CRAFTED_BAND_TOUCH_CLOSES):
        ctx.advance(bar, _flat_snapshot())

    # Strategy bands (via Context.indicator + manual rolling std)
    strat_upper, strat_middle, strat_lower = strat._bands(ctx)
    assert strat_upper is not None and strat_middle is not None and strat_lower is not None

    # Indicator bands (the IndicatorReader path)
    df = ctx.history.to_dataframe()
    bb_full = BollingerBands(period=10, std_multiplier=2.0).compute_full(df["close"])

    # Last valid index — bands should agree byte-for-byte (same SMA,
    # same ddof=0 stdev convention, same multiplier).
    assert float(strat_upper.iloc[-1]) == pytest.approx(float(bb_full["upper"].iloc[-1]))
    assert float(strat_middle.iloc[-1]) == pytest.approx(float(bb_full["middle"].iloc[-1]))
    assert float(strat_lower.iloc[-1]) == pytest.approx(float(bb_full["lower"].iloc[-1]))


# ─────────────────────────────────────────────────────────────────────
# Metadata + Protocol
# ─────────────────────────────────────────────────────────────────────


def test_strategy_metadata() -> None:
    strat = BollingerMeanRevertStrategy()
    assert strat.name == "bollinger_mean_revert"
    assert strat.version == "0.1"
    assert strat.interval == "1d"


def test_strategy_satisfies_strategy_protocol() -> None:
    from app.services.sim.strategy import Strategy
    assert isinstance(BollingerMeanRevertStrategy(), Strategy)
