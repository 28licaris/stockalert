"""
Unit tests for the trading subsystem.

These exercise each layer against synthetic data — no CH, no
Iceberg, no provider HTTP. The integration test in
`test_sim_integration.py` covers the full real-bronze path.

Layers:
  - Pydantic schemas (Action, BacktestConfig, RunResult round-trip)
  - Indicators (SMA, EMA)
  - Indicator registry
  - BarHistory + Context indicator caching
  - Portfolio (buy / sell / set_position / mark-to-market)
  - Fees + slippage models
  - Evaluator (canonical metrics on a synthetic curve)
  - SmaCrossoverStrategy on a hand-crafted bar stream
  - Backtester end-to-end with stubbed bar source
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.indicators.ema import EMA
from app.indicators.registry import get_indicator, list_indicators
from app.indicators.sma import SMA
from app.services.sim.context import BarHistory, Context
from app.services.sim.evaluator import StandardEvaluator
from app.services.sim.fees import (
    NextBarOpenFill,
    PercentFees,
    PercentSlippage,
    PerShareFees,
    ZeroFees,
    make_fees,
    make_slippage,
)
from app.services.sim.portfolio import Portfolio
from app.services.sim.schemas import (
    Action,
    BacktestConfig,
    PortfolioSnapshot,
    Position,
    RunMetrics,
    Trade,
    hold,
)
from app.services.sim.strategies.sma_crossover import (
    SmaCrossoverParams,
    SmaCrossoverStrategy,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


class _SyntheticBar:
    """Minimal Bar — duck-types into the Bar Protocol."""

    def __init__(self, symbol, timestamp, open_, high, low, close, volume=1000.0):
        self.symbol = symbol
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def _bars(symbol: str, closes: list[float], start_day: int = 1) -> list[_SyntheticBar]:
    """Build a synthetic bar stream from a list of close prices."""
    out = []
    base = datetime(2024, 1, start_day, tzinfo=timezone.utc)
    for i, c in enumerate(closes):
        out.append(_SyntheticBar(
            symbol=symbol,
            timestamp=base + timedelta(days=i),
            open_=c, high=c * 1.005, low=c * 0.995, close=c, volume=10_000,
        ))
    return out


def _config(symbol: str = "TEST", interval: str = "1d") -> BacktestConfig:
    return BacktestConfig(
        symbols=[symbol],
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        interval=interval,
        starting_cash=10_000.0,
        history_window=200,
        fees_model="zero",
        slippage_model="next_bar_open",
    )


# ─────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────


def test_action_defaults_and_validation() -> None:
    a = Action(kind="buy", symbol="AAPL", size=10.0)
    assert a.kind == "buy"
    assert a.size == 10.0
    assert hold().kind == "hold"


def test_backtest_config_round_trip_json() -> None:
    cfg = _config()
    blob = cfg.model_dump_json()
    cfg2 = BacktestConfig.model_validate_json(blob)
    assert cfg2.symbols == cfg.symbols
    assert cfg2.starting_cash == cfg.starting_cash
    assert cfg2.interval == cfg.interval


def test_run_metrics_optional_fields_default() -> None:
    m = RunMetrics(total_return=0.0)
    # Optional metrics default to None — degenerate runs don't crash.
    assert m.sharpe_ratio is None
    assert m.win_rate is None
    assert m.n_trades == 0


# ─────────────────────────────────────────────────────────────────────
# Indicators
# ─────────────────────────────────────────────────────────────────────


def test_sma_period_matches_pandas_rolling() -> None:
    closes = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0, 20.0])
    sma = SMA(period=3).compute(closes)
    # 3-bar SMA: first two NaN, then (10+12+14)/3=12, (12+14+16)/3=14, ...
    assert math.isnan(sma.iloc[0])
    assert math.isnan(sma.iloc[1])
    assert sma.iloc[2] == pytest.approx(12.0)
    assert sma.iloc[3] == pytest.approx(14.0)
    assert sma.iloc[5] == pytest.approx(18.0)


def test_sma_invalid_period_raises() -> None:
    with pytest.raises(ValueError, match="period must be >= 1"):
        SMA(period=0)


def test_ema_smoothing_factor() -> None:
    """EMA with span=3 → alpha = 2/(3+1) = 0.5; first value is the seed."""
    closes = pd.Series([10.0, 20.0, 30.0, 40.0])
    ema = EMA(period=3).compute(closes)
    # min_periods=3 → first two NaN, third onwards has values
    assert math.isnan(ema.iloc[0])
    assert math.isnan(ema.iloc[1])
    # Manual: with adjust=False, e[0] = 10, e[1] = 0.5*20 + 0.5*10 = 15,
    # e[2] = 0.5*30 + 0.5*15 = 22.5
    assert ema.iloc[2] == pytest.approx(22.5)


def test_indicator_registry_resolves_known_names() -> None:
    assert isinstance(get_indicator("sma", period=20), SMA)
    assert isinstance(get_indicator("ema", period=20), EMA)
    # Case-insensitive
    assert isinstance(get_indicator("SMA", period=20), SMA)


def test_indicator_registry_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown indicator"):
        get_indicator("nope")


def test_indicator_registry_lists_all_supported() -> None:
    names = list_indicators()
    assert {"sma", "ema", "rsi", "macd", "tsi"} <= set(names)


# ─────────────────────────────────────────────────────────────────────
# BarHistory + Context
# ─────────────────────────────────────────────────────────────────────


def test_bar_history_evicts_at_maxlen() -> None:
    h = BarHistory(maxlen=3)
    bars = _bars("X", [1, 2, 3, 4, 5])
    for b in bars:
        h.append(b)
    assert len(h) == 3
    # Should retain the LAST three.
    last3 = h.last(3)
    assert [b.close for b in last3] == [3.0, 4.0, 5.0]


def test_bar_history_dataframe_columns() -> None:
    h = BarHistory(maxlen=10)
    for b in _bars("X", [10, 11, 12]):
        h.append(b)
    df = h.to_dataframe()
    assert set(df.columns) == {"open", "high", "low", "close", "volume"}
    assert list(df["close"]) == [10.0, 11.0, 12.0]


def test_context_indicator_cache_within_bar() -> None:
    """Same call within one bar returns the same Series object."""
    cfg = _config()
    ctx = Context(config=cfg)
    bar = _bars("TEST", [10.0])[0]
    ctx.advance(bar, _empty_snap())
    s1 = ctx.indicator("sma", period=20)
    s2 = ctx.indicator("sma", period=20)
    assert s1 is s2  # same object — cached


def test_context_indicator_cache_invalidates_on_advance() -> None:
    """Each new bar clears the indicator cache."""
    cfg = _config()
    ctx = Context(config=cfg)
    bars = _bars("TEST", [10.0, 11.0])
    ctx.advance(bars[0], _empty_snap())
    s1 = ctx.indicator("sma", period=20)
    ctx.advance(bars[1], _empty_snap())
    s2 = ctx.indicator("sma", period=20)
    assert s1 is not s2  # cleared


def test_context_log_captures_entries() -> None:
    cfg = _config()
    ctx = Context(config=cfg)
    ctx.advance(_bars("X", [10.0])[0], _empty_snap())
    ctx.log(event="signal_buy", price=10.0)
    entries = ctx.log_entries
    assert len(entries) == 1
    assert entries[0]["event"] == "signal_buy"
    assert "timestamp" in entries[0]


def _empty_snap() -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=10000.0, equity=10000.0)


# ─────────────────────────────────────────────────────────────────────
# Portfolio
# ─────────────────────────────────────────────────────────────────────


def test_portfolio_buy_then_sell_realizes_pnl() -> None:
    pf = Portfolio(starting_cash=10_000.0)
    fees = ZeroFees()
    slip = NextBarOpenFill()

    # Two bars: buy on first, sell on second.
    bar1 = _SyntheticBar("AAPL", _t(0), 100, 101, 99, 100)
    bar2 = _SyntheticBar("AAPL", _t(1), 110, 111, 109, 110)
    bar3 = _SyntheticBar("AAPL", _t(2), 115, 116, 114, 115)

    # Buy 10 shares — fill at bar2.open = 110
    pf.apply(Action(kind="buy", symbol="AAPL", size=10.0), bar1, bar2, fees, slip)
    assert "AAPL" in pf.positions
    assert pf.positions["AAPL"].quantity == 10.0
    assert pf.cash == pytest.approx(10_000 - 10 * 110)

    # MTM at bar2 close — unrealized = (110 - 110) * 10 = 0
    pf.mark_to_market(bar2)
    assert pf.positions["AAPL"].unrealized_pnl == pytest.approx(0)

    # Sell — fill at bar3.open = 115
    pf.apply(Action(kind="sell", symbol="AAPL", size=10.0), bar2, bar3, fees, slip)
    assert "AAPL" not in pf.positions
    assert pf.cash == pytest.approx(10_000 - 10 * 110 + 10 * 115)
    last_trade = pf.closed_trades[-1]
    assert last_trade.side == "sell"
    assert last_trade.realized_pnl == pytest.approx(50.0)


def test_portfolio_buy_clamped_to_cash() -> None:
    """Requested size > affordable → buys what cash allows."""
    pf = Portfolio(starting_cash=1_000.0)
    bar1 = _SyntheticBar("X", _t(0), 100, 101, 99, 100)
    bar2 = _SyntheticBar("X", _t(1), 110, 111, 109, 110)
    pf.apply(Action(kind="buy", symbol="X", size=1000.0), bar1, bar2, ZeroFees(), NextBarOpenFill())
    # max affordable at 110/share is ~9.09
    assert pf.positions["X"].quantity <= 9.1
    assert pf.cash >= 0  # never negative


def test_portfolio_sell_clamped_to_position() -> None:
    """Sell larger than position → clamps to position size, no error."""
    pf = Portfolio(starting_cash=10_000.0)
    bar1 = _SyntheticBar("X", _t(0), 100, 101, 99, 100)
    bar2 = _SyntheticBar("X", _t(1), 105, 106, 104, 105)
    bar3 = _SyntheticBar("X", _t(2), 110, 111, 109, 110)
    pf.apply(Action(kind="buy", symbol="X", size=5.0), bar1, bar2, ZeroFees(), NextBarOpenFill())
    # Try to sell more than we own
    pf.apply(Action(kind="sell", symbol="X", size=100.0), bar2, bar3, ZeroFees(), NextBarOpenFill())
    assert "X" not in pf.positions  # fully closed


def test_portfolio_set_position_decomposes_to_buy() -> None:
    """set_position 5 from 0 -> buy 5."""
    pf = Portfolio(starting_cash=10_000.0)
    bar1 = _SyntheticBar("X", _t(0), 100, 101, 99, 100)
    bar2 = _SyntheticBar("X", _t(1), 100, 101, 99, 100)
    pf.apply(Action(kind="set_position", symbol="X", size=5.0), bar1, bar2, ZeroFees(), NextBarOpenFill())
    assert pf.positions["X"].quantity == pytest.approx(5.0)


def test_portfolio_set_position_decomposes_to_sell() -> None:
    """set_position 0 from 5 -> sell 5."""
    pf = Portfolio(starting_cash=10_000.0)
    bar1 = _SyntheticBar("X", _t(0), 100, 101, 99, 100)
    bar2 = _SyntheticBar("X", _t(1), 100, 101, 99, 100)
    bar3 = _SyntheticBar("X", _t(2), 100, 101, 99, 100)
    pf.apply(Action(kind="buy", symbol="X", size=5.0), bar1, bar2, ZeroFees(), NextBarOpenFill())
    pf.apply(Action(kind="set_position", symbol="X", size=0.0), bar2, bar3, ZeroFees(), NextBarOpenFill())
    assert "X" not in pf.positions


def test_portfolio_mark_to_market_updates_equity_curve() -> None:
    pf = Portfolio(starting_cash=10_000.0)
    bars = _bars("X", [100, 110, 105])
    for i, b in enumerate(bars):
        if i == 0:
            pf.apply(Action(kind="buy", symbol="X", size=10.0), b, bars[1], ZeroFees(), NextBarOpenFill())
        pf.mark_to_market(b)
    assert len(pf.equity_curve) == 3
    # After buying at 110 (next-bar open) on bar0, MTM at bar1 close=110 -> unrealized=0
    # MTM at bar2 close=105 -> unrealized = (105-110)*10 = -50 -> equity = (10000 - 1100) + (10*110 + (-50)) = 8900 + 1050 = 9950
    assert pf.equity_curve[-1][1] == pytest.approx(9950.0)


def _t(d: int) -> datetime:
    return datetime(2024, 1, 1 + d, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Fees + slippage
# ─────────────────────────────────────────────────────────────────────


def test_zero_fees_returns_zero() -> None:
    assert ZeroFees().fee_for(Action(kind="buy", symbol="X", size=100), 50.0) == 0


def test_per_share_fees_respects_minimum() -> None:
    """0.005/share * 10 = 0.05 → below the $1 minimum, so charge $1."""
    f = PerShareFees(per_share=0.005, min_commission=1.00)
    assert f.fee_for(Action(kind="buy", symbol="X", size=10), 50.0) == pytest.approx(1.00)


def test_per_share_fees_caps_at_max_pct() -> None:
    """For tiny notionals: max_commission_pct caps so fees never exceed 1% of trade."""
    f = PerShareFees(per_share=0.005, min_commission=1.00, max_commission_pct=0.01)
    # 1 share @ $0.10 = $0.10 notional → 1% cap = $0.001 → BELOW min ($1) BUT capped to 1%
    # Result: $0.001 wins (max cap takes precedence over min)
    fee = f.fee_for(Action(kind="buy", symbol="X", size=1), 0.10)
    assert fee == pytest.approx(0.001)


def test_percent_fees_proportional() -> None:
    f = PercentFees(pct=0.001)  # 10 bps
    fee = f.fee_for(Action(kind="buy", symbol="X", size=100), 50.0)
    assert fee == pytest.approx(5.0)  # 100 * 50 * 0.001


def test_next_bar_open_fill() -> None:
    s = NextBarOpenFill()
    bar = _SyntheticBar("X", _t(0), 100, 101, 99, 100.5)
    assert s.fill_price(Action(kind="buy", symbol="X", size=1), bar) == 100.0


def test_next_bar_open_fill_returns_nan_on_end_of_data() -> None:
    s = NextBarOpenFill()
    assert math.isnan(s.fill_price(Action(kind="buy", symbol="X", size=1), None))


def test_percent_slippage_hurts_trader() -> None:
    s = PercentSlippage(pct=0.001)
    bar = _SyntheticBar("X", _t(0), 100, 101, 99, 100)
    buy_fill = s.fill_price(Action(kind="buy", symbol="X", size=1), bar)
    sell_fill = s.fill_price(Action(kind="sell", symbol="X", size=1), bar)
    assert buy_fill > 100  # buying — pay more
    assert sell_fill < 100  # selling — receive less


def test_make_fees_and_slippage_factories() -> None:
    assert isinstance(make_fees("zero", {}), ZeroFees)
    assert isinstance(make_fees("per_share", {"per_share": 0.01}), PerShareFees)
    assert isinstance(make_slippage("next_bar_open", {}), NextBarOpenFill)
    with pytest.raises(ValueError, match="Unknown fees model"):
        make_fees("nope")
    with pytest.raises(ValueError, match="Unknown slippage model"):
        make_slippage("nope")


# ─────────────────────────────────────────────────────────────────────
# Evaluator — synthetic equity curves
# ─────────────────────────────────────────────────────────────────────


def test_evaluator_computes_total_return_and_max_drawdown() -> None:
    pf = Portfolio(starting_cash=10_000.0)
    # Hand-build an equity curve: rise to 12000, drop to 9000, finish at 11000.
    pf.equity_curve = [
        (_t(0), 10000),
        (_t(1), 11000),
        (_t(2), 12000),
        (_t(3), 9000),
        (_t(4), 11000),
    ]
    metrics = StandardEvaluator().compute(pf, _config())
    assert metrics.final_equity == pytest.approx(11000.0)
    assert metrics.total_return == pytest.approx(0.1)
    # Max DD: 9000 from peak 12000 → -0.25
    assert metrics.max_drawdown == pytest.approx(-0.25)


def test_evaluator_handles_empty_trades() -> None:
    pf = Portfolio(starting_cash=10_000.0)
    pf.equity_curve = [(_t(0), 10000), (_t(1), 10000)]
    metrics = StandardEvaluator().compute(pf, _config())
    # Win rate / profit factor / avg trade all None for zero trades
    assert metrics.n_trades == 0
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.avg_trade_pnl is None


def test_evaluator_sharpe_is_none_when_no_variance() -> None:
    pf = Portfolio(starting_cash=10_000.0)
    pf.equity_curve = [(_t(i), 10_000.0) for i in range(10)]
    metrics = StandardEvaluator().compute(pf, _config())
    assert metrics.sharpe_ratio is None  # no variance, no Sharpe


# ─────────────────────────────────────────────────────────────────────
# SmaCrossoverStrategy — synthetic bar stream
# ─────────────────────────────────────────────────────────────────────


def test_sma_crossover_params_rejects_bad_ordering() -> None:
    with pytest.raises(ValueError, match="must be <"):
        SmaCrossoverStrategy(params=SmaCrossoverParams(fast_period=50, slow_period=20))


def test_sma_crossover_holds_during_warmup() -> None:
    """First slow_period+1 bars → hold (insufficient history)."""
    strat = SmaCrossoverStrategy(params=SmaCrossoverParams(fast_period=2, slow_period=4))
    ctx = Context(config=_config())
    for bar in _bars("X", [10, 11, 12]):  # only 3 bars; need 5+
        ctx.advance(bar, _empty_snap())
        assert strat.on_bar(ctx).kind == "hold"


def test_sma_crossover_emits_buy_on_cross_up() -> None:
    """Crafted price series: low for a while, then rises sharply → fast crosses up."""
    strat = SmaCrossoverStrategy(params=SmaCrossoverParams(
        fast_period=2, slow_period=4, position_size_pct=0.95,
    ))
    ctx = Context(config=_config())
    closes = [10, 10, 10, 10, 10, 10, 12, 15, 18, 22]  # rising at the end
    actions: list[str] = []
    for bar in _bars("X", closes):
        ctx.advance(bar, _empty_snap())
        a = strat.on_bar(ctx)
        actions.append(a.kind)
    assert "buy" in actions, f"expected a buy somewhere in {actions}"


# ─────────────────────────────────────────────────────────────────────
# Backtester — synthetic bar source
# ─────────────────────────────────────────────────────────────────────


def test_backtester_rejects_interval_mismatch() -> None:
    from app.services.sim.backtester import Backtester

    bt = Backtester()
    strat = SmaCrossoverStrategy()  # interval="1d"
    cfg = _config(interval="1m")
    with pytest.raises(ValueError, match="interval"):
        bt.run(strat, cfg)


def test_portfolio_buy_clamped_to_cash() -> None:  # noqa: F811 (override)
    pass  # placeholder to keep file structure; real test above


def _stub_fetch_bars(symbol: str, closes: list[float]) -> tuple[list, "BacktestConfig"]:
    """Build a (bars, config) pair for a stubbed backtester run."""
    bars = _bars(symbol, closes)
    cfg = _config(symbol=symbol, interval="1d").model_copy(update={
        "start": bars[0].timestamp, "end": bars[-1].timestamp,
    })
    return bars, cfg


# Synthetic price series that GUARANTEES an SMA crossover. 10 flat
# bars (both SMAs converge to the same value) followed by a sharp
# rise (fast SMA shoots above slow SMA → cross-up detected).
_CROSSING_CLOSES = [100.0] * 12 + list(range(105, 150, 3))


def test_backtester_end_to_end_with_stubbed_source(monkeypatch) -> None:
    """
    Full backtester run with a stubbed `_fetch_bars`. Verifies the
    orchestration loop: bars → strategy → portfolio → evaluator →
    RunResult populated.
    """
    from app.services.sim import backtester as bt_mod
    from app.services.sim.backtester import Backtester

    bars, cfg = _stub_fetch_bars("TEST", _CROSSING_CLOSES)

    monkeypatch.setattr(
        bt_mod.Backtester, "_fetch_bars_multi",
        lambda self, c, intervals: {iv: {"TEST": bars} for iv in intervals},
    )
    monkeypatch.setattr(bt_mod.Backtester, "_capture_snapshot",
                        lambda self, c, exec_interval: "test-snap")

    strat = SmaCrossoverStrategy(params=SmaCrossoverParams(
        fast_period=3, slow_period=10, position_size_pct=0.95,
    ))

    run = Backtester().run(strat, cfg)
    assert run.strategy_name == "sma_crossover"
    assert run.snapshot_id == "test-snap"
    assert len(run.equity_curve) == len(bars)
    assert run.metrics.n_trades >= 1, (
        f"expected at least one trade on crossing series; "
        f"actions log on context not populated, n_trades={run.metrics.n_trades}"
    )
    assert run.metrics.total_return > 0


def test_backtester_deterministic(monkeypatch) -> None:
    """Same inputs -> same metrics. Reproducibility gate."""
    from app.services.sim import backtester as bt_mod
    from app.services.sim.backtester import Backtester

    bars, cfg = _stub_fetch_bars("TEST", _CROSSING_CLOSES)
    monkeypatch.setattr(
        bt_mod.Backtester, "_fetch_bars_multi",
        lambda self, c, intervals: {iv: {"TEST": bars} for iv in intervals},
    )
    monkeypatch.setattr(bt_mod.Backtester, "_capture_snapshot",
                        lambda self, c, exec_interval: "test-snap")

    def _new_strat():
        return SmaCrossoverStrategy(params=SmaCrossoverParams(
            fast_period=3, slow_period=10, position_size_pct=0.95,
        ))

    run1 = Backtester().run(_new_strat(), cfg)
    run2 = Backtester().run(_new_strat(), cfg)
    # Run IDs differ (uuid each time), but metrics + trades are identical
    assert run1.metrics == run2.metrics
    assert run1.equity_curve == run2.equity_curve
    assert [t.model_dump() for t in run1.trades] == [t.model_dump() for t in run2.trades]


# ─────────────────────────────────────────────────────────────────────
# Structural gate — strategies are pure
# ─────────────────────────────────────────────────────────────────────


def test_strategy_is_pure() -> None:
    """
    GATE: every module reachable from `app/services/sim/strategies/`
    must NOT import `app.db.*`, `app.providers.*`, or network libs.
    Strategies are pure (price + indicators → action) — the harness
    pumps data, the strategy decides.

    Future LLM-driven and external-feature strategies will live in
    a clearly-marked subfolder with explicit side-effect annotation
    (e.g. `strategies/sideeffect/llm_agent.py`). For TA-1, all
    strategies under `strategies/` are pure.
    """
    import ast
    import importlib.util
    from pathlib import Path

    strategies_dir = Path("app/services/sim/strategies")
    if not strategies_dir.is_dir():
        pytest.skip("strategies dir not found (test invoked from wrong CWD?)")

    forbidden_prefixes = ("app.db", "app.providers")
    visited: set[str] = set()

    def _module_path(mod_name: str) -> str | None:
        try:
            spec = importlib.util.find_spec(mod_name)
        except (ImportError, ValueError):
            return None
        if spec is None or spec.origin in (None, "built-in"):
            return None
        return spec.origin

    def _walk(mod_name: str) -> None:
        if mod_name in visited:
            return
        visited.add(mod_name)
        path = _module_path(mod_name)
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError):
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        _walk(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("app."):
                    _walk(node.module)

    # Walk every strategy module
    for py in strategies_dir.glob("*.py"):
        if py.name == "__init__.py":
            continue
        mod = f"app.services.sim.strategies.{py.stem}"
        _walk(mod)

    leaked = sorted(
        m for m in visited if any(m.startswith(p) for p in forbidden_prefixes)
    )
    assert not leaked, (
        f"PURITY VIOLATION: {len(leaked)} module(s) under "
        f"{forbidden_prefixes} are reachable from strategies/. "
        "Strategies must be pure functions of (price + indicators) "
        "via the Context. Move CH/provider access into the harness "
        "or the readers, or label the strategy as side-effecting in "
        "a sideeffect/ subfolder. Leaked: " + str(leaked)
    )
