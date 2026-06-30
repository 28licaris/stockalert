"""
SignalSource — the pluggable "what's the setup right now?" layer.

A SignalSource answers one question per bar: *given everything knowable up
to and including this bar, is there a trade setup here?* It returns a
`Signal` (entry / stop / target plan) or None. It does NOT manage positions,
sizing, or exits — that's `AlertStrategy`'s job. This split is the modular
axis of the R&D platform (see docs/strategy_rnd_platform_design.md):

    new strategy idea  ==  new SignalSource   (same execution + sizing + exits)

No-look-ahead by construction: sources read only `Context` (current bar +
rolling history + indicators), which the backtester guarantees contains
nothing past the current bar. Sources are **pure** — no db/provider/network
imports — so they run identically in backtest and (later) live paper trading.

Sources implemented here are computed-on-the-fly (MA cross, breakout). Sources
that need a heavier upstream pipeline (e.g. Elliott Wave as-of labeling) will
be injected as a precomputed signal map behind the same protocol — a follow-up;
the `AlertStrategy` contract does not change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import pandas as pd

from app.services.sim.context import Context


@dataclass(frozen=True)
class Signal:
    """
    A self-contained long trade plan emitted by a SignalSource.

    `entry` is a reference price (typically the signal bar's close); the actual
    fill happens at the next bar's open per the backtester's slippage model.
    `stop` < `entry` < `target_1` for a long. `confidence` is source-defined
    (0..1) and lets the A+ scoring layer (Milestone 2) rank/filter signals.
    Short setups are represented with direction='short' but are skipped by the
    long-only executor until shorting lands (engine reserves quantity<0).
    """

    symbol: str
    direction: str            # "long" | "short"
    entry: float
    stop: float
    target_1: float
    target_2: Optional[float] = None
    confidence: float = 0.0
    kind: str = ""            # "ma_cross", "breakout", ...
    rationale: str = ""

    @property
    def risk_per_share(self) -> float:
        """Entry-to-stop distance (long). Always positive for a valid long."""
        return self.entry - self.stop

    @property
    def reward_risk(self) -> float:
        """(target_1 - entry) / (entry - stop). 0 if risk is non-positive."""
        r = self.risk_per_share
        return (self.target_1 - self.entry) / r if r > 0 else 0.0


@runtime_checkable
class SignalSource(Protocol):
    """A per-bar setup detector. `name` is recorded for traceability."""

    name: str

    def setup(self, ctx: Context) -> None: ...
    def on_bar(self, ctx: Context) -> Optional[Signal]: ...


class BaseSignalSource:
    """No-op lifecycle so concrete sources only implement `on_bar`."""

    name: str = "base"

    def setup(self, ctx: Context) -> None:  # noqa: D401
        """Default: no state to reset."""

    def on_bar(self, ctx: Context) -> Optional[Signal]:  # pragma: no cover
        return None


# ─────────────────────────────────────────────────────────────────────
# Concrete sources (pure; computed from Context only)
# ─────────────────────────────────────────────────────────────────────


class MACrossSignalSource(BaseSignalSource):
    """
    Long setup when a fast MA crosses above a slow MA. Stop = a fixed % below
    entry; target = entry + reward_risk_mult × risk. Deliberately mechanical —
    it exists to exercise the bridge with a deterministic, visible signal.
    """

    name = "ma_cross"

    def __init__(
        self,
        *,
        fast_period: int = 20,
        slow_period: int = 50,
        ma: str = "ema",
        stop_pct: float = 0.05,
        reward_risk_mult: float = 2.0,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError(f"fast_period ({fast_period}) must be < slow_period ({slow_period})")
        if not (0.0 < stop_pct < 1.0):
            raise ValueError(f"stop_pct must be in (0,1), got {stop_pct}")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.ma = ma
        self.stop_pct = stop_pct
        self.reward_risk_mult = reward_risk_mult

    def on_bar(self, ctx: Context) -> Optional[Signal]:
        if len(ctx.history) < self.slow_period + 1:
            return None
        fast = ctx.indicator(self.ma, period=self.fast_period)
        slow = ctx.indicator(self.ma, period=self.slow_period)
        fast_now, fast_prev = _last_two(fast)
        slow_now, slow_prev = _last_two(slow)
        if any(_isnan(v) for v in (fast_now, fast_prev, slow_now, slow_prev)):
            return None
        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        if not crossed_up:
            return None
        entry = float(ctx.bar.close)
        if entry <= 0:
            return None
        stop = entry * (1.0 - self.stop_pct)
        target = entry + self.reward_risk_mult * (entry - stop)
        return Signal(
            symbol=ctx.bar.symbol, direction="long",
            entry=entry, stop=stop, target_1=target,
            confidence=0.5, kind="ma_cross",
            rationale=f"{self.ma}{self.fast_period} crossed above {self.ma}{self.slow_period}",
        )


class BreakoutSignalSource(BaseSignalSource):
    """
    Long setup when price breaks above the highest close of the prior
    `lookback` bars on a volume expansion ("going on a run"). Stop = the
    breakout level (prior high); target = entry + reward_risk_mult × risk.
    The classic momentum/breakout setup the options track will later trade.
    """

    name = "breakout"

    def __init__(
        self,
        *,
        lookback: int = 20,
        vol_mult: float = 1.5,
        vol_avg_period: int = 20,
        reward_risk_mult: float = 2.0,
        min_risk_pct: float = 0.005,
        trend_filter: bool = False,
        trend_ma: str = "sma",
        trend_period: int = 50,
    ) -> None:
        if lookback < 2:
            raise ValueError(f"lookback must be >= 2, got {lookback}")
        self.lookback = lookback
        self.vol_mult = vol_mult
        self.vol_avg_period = vol_avg_period
        self.reward_risk_mult = reward_risk_mult
        self.min_risk_pct = min_risk_pct
        # Confluence: only take breakouts while price is above a trend MA. The
        # first composable A+ filter (M2 generalizes filters into their own layer).
        self.trend_filter = trend_filter
        self.trend_ma = trend_ma
        self.trend_period = trend_period

    def on_bar(self, ctx: Context) -> Optional[Signal]:
        need = max(self.lookback, self.vol_avg_period) + 1
        if self.trend_filter:
            need = max(need, self.trend_period + 1)
        if len(ctx.history) < need:
            return None
        if self.trend_filter:
            trend = ctx.indicator(self.trend_ma, period=self.trend_period)
            trend_now = float(trend.iloc[-1]) if len(trend) else float("nan")
            # Skip breakouts that aren't confirmed by an uptrend (NaN warmup skips too).
            if trend_now != trend_now or float(ctx.bar.close) <= trend_now:
                return None
        df = ctx.history.to_dataframe()
        closes = df["close"]
        highs = df["high"] if "high" in df else closes
        # Prior window EXCLUDES the current bar — no look-ahead.
        prior_high = float(highs.iloc[-(self.lookback + 1):-1].max())
        entry = float(ctx.bar.close)
        if entry <= prior_high or entry <= 0:
            return None
        # Volume expansion confirmation (skip gate if volume is unavailable).
        if "volume" in df:
            vol_now = float(df["volume"].iloc[-1])
            vol_avg = float(df["volume"].iloc[-(self.vol_avg_period + 1):-1].mean())
            if vol_avg > 0 and vol_now < self.vol_mult * vol_avg:
                return None
        stop = prior_high
        risk = entry - stop
        if risk < entry * self.min_risk_pct:
            # Breakout too marginal — stop would be a hair under entry.
            stop = entry * (1.0 - self.min_risk_pct)
            risk = entry - stop
        target = entry + self.reward_risk_mult * risk
        return Signal(
            symbol=ctx.bar.symbol, direction="long",
            entry=entry, stop=stop, target_1=target,
            confidence=0.5, kind="breakout",
            rationale=f"close {entry:.2f} broke {self.lookback}-bar high {prior_high:.2f} on volume",
        )


# ─────────────────────────────────────────────────────────────────────
# Registry — name → factory. New sources register here.
# ─────────────────────────────────────────────────────────────────────

_SOURCES = {
    "ma_cross": MACrossSignalSource,
    "breakout": BreakoutSignalSource,
}


def build_signal_source(name: str, **params) -> SignalSource:
    """Construct a registered SignalSource by name. Raises on unknown name."""
    try:
        cls = _SOURCES[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown signal source {name!r}. One of: {', '.join(sorted(_SOURCES))}."
        ) from exc
    return cls(**params)


def list_signal_sources() -> list[str]:
    return sorted(_SOURCES)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _last_two(series: pd.Series) -> tuple[float, float]:
    if len(series) < 2:
        return (float("nan"), float("nan"))
    return (float(series.iloc[-1]), float(series.iloc[-2]))


def _isnan(v: float) -> bool:
    return v != v
