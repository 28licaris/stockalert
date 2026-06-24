"""
TA-4.1 — multi-timeframe Context + Backtester tests.

Covers four classes of behavior:

  1. **Interval helpers** — duration math, ordering validation.
  2. **Context multi-TF** — per-interval BarHistory, cross-interval
     indicator cache, advance_coarser semantics.
  3. **Backtester multi-TF iteration** — coarser bars released only
     when ready_time has passed (the no-look-ahead invariant).
  4. **End-to-end** — a multi-TF strategy runs through the Backtester
     against synthetic bars and produces a coherent RunResult.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from app.services.sim import backtester as bt_mod
from app.services.sim.backtester import Backtester
from app.services.sim.context import BarHistory, Context
from app.services.sim.intervals import (
    execution_interval,
    interval_duration,
    interval_seconds,
    supported_intervals,
    validate_intervals_order,
)
from app.services.sim.schemas import (
    Action,
    BacktestConfig,
    PortfolioSnapshot,
    hold,
)
from app.services.sim.strategy import BaseStrategy, Strategy, required_intervals


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


def _daily_bars(symbol: str, n: int, start_day: int = 1) -> list[_SyntheticBar]:
    base = datetime(2024, 8, start_day, tzinfo=timezone.utc)
    return [
        _SyntheticBar(
            symbol=symbol,
            ts=base + timedelta(days=i),
            open_=100.0 + i, high=100.5 + i, low=99.5 + i, close=100.0 + i,
            volume=1_000_000,
        )
        for i in range(n)
    ]


def _hourly_bars_for_one_day(symbol: str, day_ts: datetime) -> list[_SyntheticBar]:
    """A full UTC-day worth of hourly bars at 1h cadence."""
    return [
        _SyntheticBar(
            symbol=symbol,
            ts=day_ts + timedelta(hours=h),
            open_=200.0 + h, high=200.5 + h, low=199.5 + h, close=200.0 + h,
            volume=10_000,
        )
        for h in range(24)
    ]


def _mtf_config(interval: str = "1h", intervals: list[str] | None = None) -> BacktestConfig:
    return BacktestConfig(
        symbols=["TEST"],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 8, 7, tzinfo=timezone.utc),
        interval=interval,
        intervals=intervals,
        starting_cash=10_000.0,
        history_window=100,
    )


# ─────────────────────────────────────────────────────────────────────
# Interval helpers
# ─────────────────────────────────────────────────────────────────────


def test_interval_seconds_known_values() -> None:
    assert interval_seconds("1m") == 60
    assert interval_seconds("5m") == 300
    assert interval_seconds("1h") == 3600
    assert interval_seconds("1d") == 86400


def test_interval_seconds_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown interval"):
        interval_seconds("weekly")


def test_interval_duration_is_timedelta() -> None:
    assert interval_duration("1d") == timedelta(days=1)
    assert interval_duration("1h") == timedelta(hours=1)


def test_supported_intervals_sorted_coarsest_first() -> None:
    ivs = supported_intervals()
    for i in range(len(ivs) - 1):
        assert interval_seconds(ivs[i]) > interval_seconds(ivs[i + 1])


def test_validate_intervals_order_accepts_coarsest_to_finest() -> None:
    validate_intervals_order(["1d"])
    validate_intervals_order(["1d", "1h", "5m"])
    validate_intervals_order(["1d", "4h", "30m", "1m"])


def test_validate_intervals_order_rejects_wrong_order() -> None:
    with pytest.raises(ValueError, match="coarsest-to-finest"):
        validate_intervals_order(["1h", "1d"])
    with pytest.raises(ValueError, match="coarsest-to-finest"):
        validate_intervals_order(["1d", "5m", "1h"])


def test_validate_intervals_order_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_intervals_order(["1d", "1h", "1h"])


def test_validate_intervals_order_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        validate_intervals_order([])


def test_execution_interval_is_last() -> None:
    assert execution_interval(["1d", "1h", "5m"]) == "5m"
    assert execution_interval(["1d"]) == "1d"


# ─────────────────────────────────────────────────────────────────────
# required_intervals helper
# ─────────────────────────────────────────────────────────────────────


def test_required_intervals_single_tf_falls_back_to_interval() -> None:
    class _SingleTf:
        name = "x"
        version = "0"
        interval = "1d"
    assert required_intervals(_SingleTf()) == ["1d"]


def test_required_intervals_multi_tf_uses_intervals_attr() -> None:
    class _MultiTf:
        name = "x"
        version = "0"
        interval = "5m"
        intervals = ["1d", "1h", "5m"]
    assert required_intervals(_MultiTf()) == ["1d", "1h", "5m"]


# ─────────────────────────────────────────────────────────────────────
# Context — multi-TF API surface
# ─────────────────────────────────────────────────────────────────────


def test_context_default_single_tf() -> None:
    """No `intervals` kwarg → single-TF using config.interval."""
    cfg = _mtf_config(interval="1d")
    ctx = Context(config=cfg)
    assert ctx.intervals == ["1d"]
    assert ctx.execution_interval == "1d"


def test_context_multi_tf_init() -> None:
    cfg = _mtf_config(interval="5m", intervals=["1d", "1h", "5m"])
    ctx = Context(config=cfg, intervals=["1d", "1h", "5m"])
    assert ctx.intervals == ["1d", "1h", "5m"]
    assert ctx.execution_interval == "5m"
    # Each interval has its own BarHistory.
    assert isinstance(ctx.history_at("1d"), BarHistory)
    assert isinstance(ctx.history_at("1h"), BarHistory)
    assert isinstance(ctx.history_at("5m"), BarHistory)


def test_context_history_at_unknown_interval_raises() -> None:
    cfg = _mtf_config(intervals=["1d", "1h"])
    ctx = Context(config=cfg, intervals=["1d", "1h"])
    with pytest.raises(ValueError, match="not declared"):
        ctx.history_at("5m")


def test_context_history_property_is_execution_interval() -> None:
    """Back-compat: `ctx.history` (no args) returns execution-interval history."""
    cfg = _mtf_config(intervals=["1d", "1h"])
    ctx = Context(config=cfg, intervals=["1d", "1h"])
    # `history` should be the SAME object as history_at(execution_interval).
    assert ctx.history is ctx.history_at("1h")


def test_context_advance_coarser_rejects_execution_interval() -> None:
    cfg = _mtf_config(intervals=["1d", "1h"])
    ctx = Context(config=cfg, intervals=["1d", "1h"])
    bar = _daily_bars("TEST", 1)[0]
    with pytest.raises(ValueError, match="advance_coarser called with the execution"):
        ctx.advance_coarser("1h", bar)


def test_context_advance_coarser_unknown_interval_raises() -> None:
    cfg = _mtf_config(intervals=["1d", "1h"])
    ctx = Context(config=cfg, intervals=["1d", "1h"])
    bar = _daily_bars("TEST", 1)[0]
    with pytest.raises(ValueError, match="not declared"):
        ctx.advance_coarser("5m", bar)


def test_context_indicator_cache_keyed_per_interval() -> None:
    """SMA(20) on daily and SMA(20) on 5m must NOT collide in cache."""
    cfg = _mtf_config(intervals=["1d", "5m"])
    ctx = Context(config=cfg, intervals=["1d", "5m"])

    # Seed both histories with enough bars.
    for db in _daily_bars("TEST", 25):
        ctx.advance_coarser("1d", db)
    # And one execution (5m) bar to set ctx.bar.
    five_m = _SyntheticBar(
        "TEST", datetime(2024, 8, 26, 14, 0, tzinfo=timezone.utc),
        100, 101, 99, 100,
    )
    ctx.advance(five_m, PortfolioSnapshot(cash=10_000, equity=10_000))

    s_daily = ctx.indicator("sma", period=20, interval="1d")
    s_5m = ctx.indicator("sma", period=20, interval="5m")
    # 5m history has 1 bar — series is empty/all-nan; daily has 25 bars.
    assert len(s_daily) == 25
    assert len(s_5m) == 1
    # Same key, same call → cached:
    s_daily_again = ctx.indicator("sma", period=20, interval="1d")
    assert s_daily_again is s_daily


def test_context_advance_clears_cache() -> None:
    cfg = _mtf_config(intervals=["1d", "5m"])
    ctx = Context(config=cfg, intervals=["1d", "5m"])
    for db in _daily_bars("TEST", 25):
        ctx.advance_coarser("1d", db)
    five_m = _SyntheticBar(
        "TEST", datetime(2024, 8, 26, 14, 0, tzinfo=timezone.utc),
        100, 101, 99, 100,
    )
    ctx.advance(five_m, PortfolioSnapshot(cash=10_000, equity=10_000))
    s1 = ctx.indicator("sma", period=20, interval="1d")

    # Advance to a new execution bar — cache should clear.
    five_m_next = _SyntheticBar(
        "TEST", datetime(2024, 8, 26, 14, 5, tzinfo=timezone.utc),
        101, 102, 100, 101.5,
    )
    ctx.advance(five_m_next, PortfolioSnapshot(cash=10_000, equity=10_000))
    s2 = ctx.indicator("sma", period=20, interval="1d")
    assert s2 is not s1  # recomputed object


# ─────────────────────────────────────────────────────────────────────
# Backtester — no-look-ahead invariant
# ─────────────────────────────────────────────────────────────────────


class _RecordingMtfStrategy(BaseStrategy):
    """
    Records, per execution bar, how many bars are visible in each
    interval's history. Lets tests assert exact look-ahead-safe
    visibility counts.
    """

    name = "mtf_recorder"
    version = "0.1"
    interval = "1h"
    intervals = ["1d", "1h"]

    def __init__(self) -> None:
        self.snapshots: list[dict[str, int]] = []

    def on_bar(self, ctx: Context) -> Action:
        self.snapshots.append({
            "exec_ts": ctx.clock,
            "daily_len": len(ctx.history_at("1d")),
            "hourly_len": len(ctx.history_at("1h")),
        })
        return hold()


def test_backtester_releases_coarser_bars_only_when_ready(monkeypatch) -> None:
    """
    The no-look-ahead invariant: a daily bar timestamped 2024-08-01
    (ready_time = 2024-08-02 00:00) is NOT visible to a 5-min /
    hourly strategy iterating on 2024-08-01. It IS visible starting
    from the first hourly bar on 2024-08-02.
    """
    # Two daily bars: Aug 1 and Aug 2.
    daily_bars = _daily_bars("TEST", n=2, start_day=1)
    # Hourly bars across both days (48 hours, starting at midnight 2024-08-01).
    hourly_aug1 = _hourly_bars_for_one_day(
        "TEST", datetime(2024, 8, 1, tzinfo=timezone.utc),
    )
    hourly_aug2 = _hourly_bars_for_one_day(
        "TEST", datetime(2024, 8, 2, tzinfo=timezone.utc),
    )
    hourly_bars = hourly_aug1 + hourly_aug2

    def _fake_fetch(self, config, intervals):
        return {
            "1d": {"TEST": daily_bars},
            "1h": {"TEST": hourly_bars},
        }
    monkeypatch.setattr(bt_mod.Backtester, "_fetch_bars_multi", _fake_fetch)
    monkeypatch.setattr(
        bt_mod.Backtester, "_capture_snapshot",
        lambda self, c, exec_interval: None,
    )

    cfg = _mtf_config(interval="1h", intervals=["1d", "1h"])
    cfg = cfg.model_copy(update={
        "start": hourly_bars[0].timestamp,
        "end": hourly_bars[-1].timestamp,
    })
    strat = _RecordingMtfStrategy()
    Backtester().run(strat, cfg)

    assert len(strat.snapshots) == 48  # 48 hourly bars
    # Hour 0 (2024-08-01 00:00): NO daily yet (Aug 1 bar's ready time is Aug 2 00:00).
    assert strat.snapshots[0]["daily_len"] == 0
    # Hour 23 (2024-08-01 23:00): still no daily.
    assert strat.snapshots[23]["daily_len"] == 0
    # Hour 24 (2024-08-02 00:00): Aug 1's daily bar becomes ready.
    assert strat.snapshots[24]["daily_len"] == 1
    # Hour 47 (2024-08-02 23:00): still only 1 daily (Aug 2's bar
    # isn't ready until Aug 3 00:00).
    assert strat.snapshots[47]["daily_len"] == 1


def test_backtester_strategy_interval_must_match_execution(monkeypatch) -> None:
    """
    Strategy declares interval='1h' but config.intervals=['1d','5m']
    → execution interval is '5m', which doesn't match '1h'. Raises.
    """
    cfg = _mtf_config(interval="5m", intervals=["1d", "5m"])

    class _BadStrategy(BaseStrategy):
        name = "bad"
        version = "0.1"
        interval = "1h"  # mismatch!
        intervals = ["1d", "5m"]  # exec = '5m'

        def on_bar(self, ctx):
            return hold()

    with pytest.raises(ValueError, match="match the FINEST interval"):
        Backtester().run(_BadStrategy(), cfg)


def test_backtester_config_interval_must_match_execution(monkeypatch) -> None:
    """Same check on the config side."""
    cfg = _mtf_config(interval="1d", intervals=["1d", "5m"])

    class _OkStrategy(BaseStrategy):
        name = "ok"
        version = "0.1"
        interval = "5m"
        intervals = ["1d", "5m"]
        def on_bar(self, ctx):
            return hold()

    with pytest.raises(ValueError, match="doesn't match execution interval"):
        Backtester().run(_OkStrategy(), cfg)


# ─────────────────────────────────────────────────────────────────────
# End-to-end multi-TF strategy
# ─────────────────────────────────────────────────────────────────────


def test_mtf_strategy_can_query_both_intervals(monkeypatch) -> None:
    """
    A strategy that explicitly reads from both daily and hourly
    histories produces a non-trivial run. Sanity check on the
    end-to-end shape.
    """

    class _DailyTrendHourlyEntry(BaseStrategy):
        """Buy on hourly if daily history shows uptrend."""
        name = "daily_trend_hourly_entry"
        version = "0.1"
        interval = "1h"
        intervals = ["1d", "1h"]
        n_buys = 0

        def on_bar(self, ctx):
            daily = ctx.history_at("1d")
            hourly = ctx.history_at("1h")
            if len(daily) < 2 or len(hourly) < 2:
                return hold()
            daily_df = daily.to_dataframe()
            # Uptrend: latest daily close > earliest in window.
            if daily_df["close"].iloc[-1] <= daily_df["close"].iloc[0]:
                return hold()
            position = ctx.portfolio.positions.get(ctx.bar.symbol)
            if position is not None and position.quantity > 0:
                return hold()
            qty = int(ctx.portfolio.cash * 0.5 / ctx.bar.close)
            if qty <= 0:
                return hold()
            self.n_buys += 1
            return Action(kind="buy", symbol=ctx.bar.symbol, size=float(qty),
                          note="daily-uptrend + hourly entry")

    daily_bars = _daily_bars("TEST", n=5, start_day=1)  # rising daily closes
    hourly_bars = (
        _hourly_bars_for_one_day("TEST", datetime(2024, 8, 1, tzinfo=timezone.utc))
        + _hourly_bars_for_one_day("TEST", datetime(2024, 8, 2, tzinfo=timezone.utc))
        + _hourly_bars_for_one_day("TEST", datetime(2024, 8, 3, tzinfo=timezone.utc))
    )

    def _fake_fetch(self, config, intervals):
        return {"1d": {"TEST": daily_bars}, "1h": {"TEST": hourly_bars}}
    monkeypatch.setattr(bt_mod.Backtester, "_fetch_bars_multi", _fake_fetch)
    monkeypatch.setattr(
        bt_mod.Backtester, "_capture_snapshot",
        lambda self, c, exec_interval: None,
    )

    cfg = _mtf_config(interval="1h", intervals=["1d", "1h"])
    cfg = cfg.model_copy(update={
        "start": hourly_bars[0].timestamp,
        "end": hourly_bars[-1].timestamp,
    })
    strat = _DailyTrendHourlyEntry()
    result = Backtester().run(strat, cfg)

    # Daily uptrend made visible from hour 24 onward; strategy
    # should have bought at least once.
    assert strat.n_buys >= 1
    assert result.metrics.n_trades >= 1


# ─────────────────────────────────────────────────────────────────────
# Back-compat: single-TF strategies still work unchanged
# ─────────────────────────────────────────────────────────────────────


def test_single_tf_strategy_still_runs_unchanged(monkeypatch) -> None:
    """Existing SMA Crossover (no `intervals` attr) goes through the new path cleanly."""
    from app.services.sim.strategies.sma_crossover import (
        SmaCrossoverParams,
        SmaCrossoverStrategy,
    )

    bars = _daily_bars("TEST", n=30)

    def _fake_fetch(self, config, intervals):
        # Single-TF case: intervals == ['1d']
        assert intervals == ["1d"], f"expected single interval, got {intervals}"
        return {"1d": {"TEST": bars}}
    monkeypatch.setattr(bt_mod.Backtester, "_fetch_bars_multi", _fake_fetch)
    monkeypatch.setattr(
        bt_mod.Backtester, "_capture_snapshot",
        lambda self, c, exec_interval: None,
    )

    cfg = _mtf_config(interval="1d")
    cfg = cfg.model_copy(update={
        "start": bars[0].timestamp, "end": bars[-1].timestamp,
    })
    strat = SmaCrossoverStrategy(
        params=SmaCrossoverParams(fast_period=3, slow_period=10),
        interval="1d",
    )
    # Should run without error and produce a result.
    result = Backtester().run(strat, cfg)
    assert result.strategy_name == "sma_crossover"
