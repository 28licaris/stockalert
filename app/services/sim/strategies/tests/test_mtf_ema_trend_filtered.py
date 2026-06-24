"""
Unit tests for MtfEmaTrendFilteredStrategy.

Multi-timeframe strategy = stricter test requirements than single-TF:
besides the usual entry/exit/warmup/sizing/protocol checks, we
verify the **trend-gate behavior** AND the structural no-look-ahead
invariant for the strategy's own queries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.sim import backtester as bt_mod
from app.services.sim.backtester import Backtester
from app.services.sim.context import Context
from app.services.sim.schemas import (
    BacktestConfig,
    PortfolioSnapshot,
    Position,
    hold,
)
from app.services.sim.strategies.mtf_ema_trend_filtered import (
    MtfEmaTrendFilteredParams,
    MtfEmaTrendFilteredStrategy,
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


def _daily_bars(symbol: str, closes: list[float], start_day: int = 1):
    base = datetime(2024, 6, start_day, tzinfo=timezone.utc)
    return [
        _SyntheticBar(
            symbol=symbol, ts=base + timedelta(days=i),
            open_=c, high=c * 1.005, low=c * 0.995, close=c, volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]


def _hourly_bars(symbol: str, closes: list[float], start_ts: datetime):
    return [
        _SyntheticBar(
            symbol=symbol, ts=start_ts + timedelta(hours=i),
            open_=c, high=c * 1.001, low=c * 0.999, close=c, volume=10_000,
        )
        for i, c in enumerate(closes)
    ]


def _config(start: datetime, end: datetime) -> BacktestConfig:
    return BacktestConfig(
        symbols=["TEST"],
        start=start, end=end,
        interval="1h",
        intervals=["1d", "1h"],
        starting_cash=10_000.0,
        history_window=200,
    )


def _empty_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=10_000.0, equity=10_000.0)


def _long_snapshot(qty: float, price: float = 100.0) -> PortfolioSnapshot:
    pos = Position(
        symbol="TEST", quantity=qty, avg_entry_price=price,
        entry_time=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    return PortfolioSnapshot(
        cash=5_000.0, equity=5_000.0 + qty * price, positions={"TEST": pos},
    )


# ─────────────────────────────────────────────────────────────────────
# Strategy metadata + Protocol
# ─────────────────────────────────────────────────────────────────────


def test_strategy_declares_two_intervals() -> None:
    strat = MtfEmaTrendFilteredStrategy()
    assert strat.intervals == ["1d", "1h"]
    assert strat.interval == "1h"
    assert strat.name == "mtf_ema_trend_filtered"
    assert strat.version == "0.1"


def test_strategy_satisfies_strategy_protocol() -> None:
    from app.services.sim.strategy import Strategy
    assert isinstance(MtfEmaTrendFilteredStrategy(), Strategy)


def test_required_intervals_picks_up_intervals_attr() -> None:
    from app.services.sim.strategy import required_intervals
    assert required_intervals(MtfEmaTrendFilteredStrategy()) == ["1d", "1h"]


def test_params_rejects_overlapping_fast_slow() -> None:
    with pytest.raises(ValueError, match="must be <"):
        MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
            fast_period=26, slow_period=12,
        ))


# ─────────────────────────────────────────────────────────────────────
# Trend gate behavior
# ─────────────────────────────────────────────────────────────────────


def _seed_with_daily_history(
    strat: MtfEmaTrendFilteredStrategy,
    daily_closes: list[float],
    hourly_closes: list[float],
    portfolio: PortfolioSnapshot,
) -> tuple[Context, list]:
    """
    Build a Context, advance it through daily then hourly bars,
    and return the actions emitted on each hourly bar.
    """
    cfg = _config(
        start=datetime(2024, 6, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
    )
    ctx = Context(config=cfg, intervals=["1d", "1h"])
    strat.setup(ctx)

    # Pre-load the daily history (60 daily bars).
    for db in _daily_bars("TEST", daily_closes):
        ctx.advance_coarser("1d", db)

    # Hourly bars start AFTER the last daily.
    hourly_start = datetime(2024, 6, 1, tzinfo=timezone.utc) + timedelta(
        days=len(daily_closes),
    )
    actions = []
    for hb in _hourly_bars("TEST", hourly_closes, hourly_start):
        ctx.advance(hb, portfolio)
        actions.append(strat.on_bar(ctx))
    return ctx, actions


def test_holds_during_daily_warmup() -> None:
    """Need daily_trend_period + 1 daily bars before the strategy fires."""
    strat = MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
        daily_trend_period=50, fast_period=3, slow_period=6,
    ))
    # Only 30 daily bars → SMA(50) NaN → strategy holds.
    daily_closes = [100.0] * 30
    hourly_closes = list(range(95, 105)) + list(range(105, 130))  # cross-up territory
    _, actions = _seed_with_daily_history(strat, daily_closes, hourly_closes, _empty_snapshot())
    assert all(a.kind == "hold" for a in actions)


def test_buys_only_when_daily_trend_up() -> None:
    """
    Setup: daily rises (close > SMA), hourly produces a cross-up
    → should BUY.
    """
    strat = MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
        daily_trend_period=20, fast_period=3, slow_period=6,
        position_size_pct=0.95,
    ))
    # Daily clearly above its SMA(20) at the end: flat then rising.
    daily_closes = [100.0] * 20 + [101.0, 103.0, 106.0, 110.0, 115.0]
    # Hourly: flat then sharp rise → produces EMA cross-up.
    hourly_closes = [115.0] * 8 + [116.0, 118.0, 120.0, 123.0, 127.0, 132.0, 138.0]
    _, actions = _seed_with_daily_history(strat, daily_closes, hourly_closes, _empty_snapshot())
    assert "buy" in [a.kind for a in actions], f"expected a buy; got {[a.kind for a in actions]}"


def test_skips_buy_when_daily_trend_down() -> None:
    """
    Setup: daily FALLS (close < SMA), hourly produces a cross-up
    → strategy must SKIP the buy.
    """
    strat = MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
        daily_trend_period=20, fast_period=3, slow_period=6,
        position_size_pct=0.95,
    ))
    # Daily clearly below its SMA(20) at the end: was 100 flat, now dropped.
    daily_closes = [100.0] * 20 + [99.0, 97.0, 94.0, 90.0, 85.0]
    # Hourly: setup that WOULD produce a cross-up.
    hourly_closes = [85.0] * 8 + [86.0, 88.0, 91.0, 95.0, 100.0]
    _, actions = _seed_with_daily_history(strat, daily_closes, hourly_closes, _empty_snapshot())
    assert "buy" not in [a.kind for a in actions], (
        f"daily trend filter should have BLOCKED the buy; got {[a.kind for a in actions]}"
    )


def test_exits_on_cross_down_regardless_of_trend() -> None:
    """
    Held position + hourly cross-down → SELL even if daily trend
    is still up. Exits are asymmetric on purpose (respect the
    exit signal so we don't ride a real reversal too long).
    """
    strat = MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
        daily_trend_period=20, fast_period=3, slow_period=6,
    ))
    # Daily still up at the end (rising series).
    daily_closes = [100.0] * 20 + [101.0, 103.0, 106.0, 110.0, 115.0]
    # Hourly: rising then falling → cross-down somewhere mid-series.
    hourly_closes = [110.0] * 5 + list(range(110, 130, 2)) + list(range(125, 100, -3))
    _, actions = _seed_with_daily_history(
        strat, daily_closes, hourly_closes, _long_snapshot(qty=50.0),
    )
    assert "sell" in [a.kind for a in actions]


def test_does_not_double_up_when_already_long() -> None:
    strat = MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
        daily_trend_period=20, fast_period=3, slow_period=6,
    ))
    daily_closes = [100.0] * 20 + [101.0, 103.0, 106.0, 110.0, 115.0]
    hourly_closes = [115.0] * 8 + [116.0, 118.0, 120.0, 124.0, 130.0]
    _, actions = _seed_with_daily_history(
        strat, daily_closes, hourly_closes, _long_snapshot(qty=50.0),
    )
    assert "buy" not in [a.kind for a in actions]


def test_does_not_sell_when_flat() -> None:
    strat = MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
        daily_trend_period=20, fast_period=3, slow_period=6,
    ))
    daily_closes = [100.0] * 20 + [99.0, 97.0, 94.0, 90.0, 85.0]
    hourly_closes = [85.0] * 5 + list(range(85, 70, -2))  # falling → cross-down
    _, actions = _seed_with_daily_history(strat, daily_closes, hourly_closes, _empty_snapshot())
    assert "sell" not in [a.kind for a in actions]


# ─────────────────────────────────────────────────────────────────────
# Backtester integration — no look-ahead via the harness's release rule
# ─────────────────────────────────────────────────────────────────────


def test_strategy_runs_end_to_end_under_backtester(monkeypatch) -> None:
    """
    Full Backtester.run with stubbed multi-interval bar source.
    Confirms the multi-TF strategy completes a run and produces a
    coherent RunResult (regardless of trade count on synthetic data).
    """
    daily_closes = [100.0] * 30 + [100.5 * (1.01 ** i) for i in range(40)]
    daily_bars = _daily_bars("TEST", daily_closes)
    # Hourly bars covering the last ~5 days of the daily window.
    hourly_start = daily_bars[-5].timestamp
    hourly_closes = [
        100.0 + 0.5 * (i % 8) + (i // 8) * 0.3
        for i in range(5 * 24)
    ]
    hourly_bars = _hourly_bars("TEST", hourly_closes, hourly_start)

    def _fake_fetch(self, config, intervals):
        return {"1d": {"TEST": daily_bars}, "1h": {"TEST": hourly_bars}}

    monkeypatch.setattr(bt_mod.Backtester, "_fetch_bars_multi", _fake_fetch)
    monkeypatch.setattr(
        bt_mod.Backtester, "_capture_snapshot",
        lambda self, c, exec_interval: None,
    )

    cfg = _config(start=hourly_bars[0].timestamp, end=hourly_bars[-1].timestamp)
    strat = MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(
        daily_trend_period=20, fast_period=3, slow_period=6,
    ))
    result = Backtester().run(strat, cfg)
    assert result.strategy_name == "mtf_ema_trend_filtered"
    assert len(result.equity_curve) == len(hourly_bars)


# ─────────────────────────────────────────────────────────────────────
# Structural: strategy reads daily via history_at, not just history
# ─────────────────────────────────────────────────────────────────────


def test_strategy_uses_history_at_for_daily() -> None:
    """
    Source-level invariant: this strategy MUST call
    `ctx.history_at("1d")` to read the daily trend filter.
    Failing this assertion means the strategy is reading only the
    execution-interval history — i.e. it would silently degrade to
    single-TF behavior.

    Same AST-walk pattern as `test_strategy_is_pure` — locks in a
    structural property that's hard to enforce at runtime.
    """
    import ast
    from pathlib import Path

    src = Path("app/services/sim/strategies/mtf_ema_trend_filtered.py").read_text()
    tree = ast.parse(src)

    found_history_at = False
    found_indicator_with_daily_interval = False

    for node in ast.walk(tree):
        # ctx.history_at("1d") -> Attribute(attr='history_at') with a "1d" arg
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "history_at"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "1d":
                    found_history_at = True

        # ctx.indicator(..., interval="1d") -> Call with a kwarg
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "indicator"
        ):
            for kw in node.keywords:
                if (
                    kw.arg == "interval"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == "1d"
                ):
                    found_indicator_with_daily_interval = True

    assert found_history_at, (
        "Multi-TF strategy must read daily bars via ctx.history_at('1d'). "
        "Without it, the daily trend filter degrades to execution-interval "
        "data — silent regression. Add the call."
    )
    assert found_indicator_with_daily_interval, (
        "Multi-TF strategy must compute the daily indicator via "
        "ctx.indicator(name, interval='1d', ...). The interval kwarg keys "
        "the per-interval cache and routes to the right history."
    )
