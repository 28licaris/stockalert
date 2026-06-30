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
        """Entry-to-stop distance, direction-agnostic (always positive when valid).
        Long: stop < entry. Short: stop > entry."""
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float:
        """Reward:risk, direction-aware. 0 if risk is non-positive."""
        r = self.risk_per_share
        if r <= 0:
            return 0.0
        reward = (self.target_1 - self.entry) if self.direction == "long" else (self.entry - self.target_1)
        return reward / r


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

class DivergenceSignalSource(BaseSignalSource):
    """
    Long setup on bullish RSI divergence — reuses the existing pure detectors
    in `app/signals/divergence.py`:
      - regular bullish (price lower-low, RSI higher-low) → reversal up
      - hidden bullish  (price higher-low, RSI lower-low)  → trend continuation

    No-look-ahead: pivots require `pivot_k` bars on each side, so the latest
    usable pivot lags by k bars — at the current bar we only act on a divergence
    whose second pivot is already confirmed. Debounced on the pivot timestamp so
    one divergence fires once. Stop = below the divergence's pivot low.
    """

    name = "divergence"

    def __init__(
        self,
        *,
        indicator: str = "rsi",
        ind_period: int = 14,
        lookback: int = 60,
        pivot_k: int = 3,
        kind: str = "both",           # "regular" | "hidden" | "both"
        side: str = "long",           # "long" (bullish) | "short" (bearish) | "both"
        reward_risk_mult: float = 2.0,
        stop_buffer_pct: float = 0.01,
    ) -> None:
        if kind not in ("regular", "hidden", "both"):
            raise ValueError(f"kind must be regular|hidden|both, got {kind!r}")
        if side not in ("long", "short", "both"):
            raise ValueError(f"side must be long|short|both, got {side!r}")
        self.indicator = indicator
        self.ind_period = ind_period
        self.lookback = lookback
        self.pivot_k = pivot_k
        self.kind = kind
        self.side = side
        self.reward_risk_mult = reward_risk_mult
        self.stop_buffer_pct = stop_buffer_pct
        self._last_p2: dict[str, object] = {}

    def setup(self, ctx: Context) -> None:
        self._last_p2 = {}

    def on_bar(self, ctx: Context) -> Optional[Signal]:
        need = self.lookback + self.pivot_k + self.ind_period + 5
        if len(ctx.history) < need:
            return None
        # Pure inline detection (no app.signals import) so the strategy graph
        # stays past the purity gate and carries no global-config trend filter.
        close = ctx.history.to_dataframe()["close"].tail(self.lookback)
        ind = ctx.indicator(self.indicator, period=self.ind_period)
        if not close.index.equals(ind.index):
            ind = ind.reindex(close.index)
        ind = ind.tail(self.lookback)
        prices = close.to_numpy()
        rsis = ind.to_numpy()
        ts_index = close.index

        if self.side in ("long", "both"):
            sig = self._detect(ctx, prices, rsis, ts_index, "long")
            if sig is not None:
                return sig
        if self.side in ("short", "both"):
            sig = self._detect(ctx, prices, rsis, ts_index, "short")
            if sig is not None:
                return sig
        return None

    def _detect(self, ctx, prices, rsis, ts_index, direction) -> Optional[Signal]:
        # Long divergence is built on pivot LOWS; short on pivot HIGHS.
        piv = _pivot_lows(prices, self.pivot_k) if direction == "long" else _pivot_highs(prices, self.pivot_k)
        if len(piv) < 2:
            return None
        p1, p2 = piv[-2], piv[-1]
        price1, price2, r1, r2 = prices[p1], prices[p2], rsis[p1], rsis[p2]
        if any(v != v for v in (r1, r2)):
            return None

        label = None
        if direction == "long":
            if self.kind in ("regular", "both") and price2 < price1 and r2 > r1:
                label = "regular_bullish"   # lower low price, higher low RSI → reversal up
            elif self.kind in ("hidden", "both") and price2 > price1 and r2 < r1:
                label = "hidden_bullish"    # higher low price, lower low RSI → continuation up
        else:  # short
            if self.kind in ("regular", "both") and price2 > price1 and r2 < r1:
                label = "regular_bearish"   # higher high price, lower high RSI → reversal down
            elif self.kind in ("hidden", "both") and price2 < price1 and r2 > r1:
                label = "hidden_bearish"    # lower high price, higher high RSI → continuation down
        if label is None:
            return None

        p2_ts = ts_index[p2]
        key = (ctx.bar.symbol, direction)   # symbol-keyed so one source serves a portfolio
        if self._last_p2.get(key) == p2_ts:
            return None
        self._last_p2[key] = p2_ts

        entry = float(ctx.bar.close)
        if entry <= 0:
            return None
        if direction == "long":
            stop = float(min(price2, entry)) * (1.0 - self.stop_buffer_pct)
            if stop >= entry:
                return None
            target = entry + self.reward_risk_mult * (entry - stop)
        else:
            stop = float(max(price2, entry)) * (1.0 + self.stop_buffer_pct)
            if stop <= entry:
                return None
            target = entry - self.reward_risk_mult * (stop - entry)
            if target <= 0:
                return None
        return Signal(
            symbol=ctx.bar.symbol, direction=direction,
            entry=entry, stop=stop, target_1=target,
            confidence=0.5, kind=f"divergence_{label}",
            rationale=f"{label} divergence on {self.indicator}{self.ind_period}",
        )


class ElliottWaveSource(BaseSignalSource):
    """
    "Trade the wave" — enter motive waves the way an Elliott Wave trader does.

    On each bar, label the name's wave structure as-of now (the pure, no-look-ahead
    `app.signals.elliott` engine over recent swing pivots). When the primary count
    confirms a MOTIVE leg in `entry_waves` (default wave 3 — the strongest, highest-
    conviction leg; optionally 5 / C), enter in the count's direction with:

      - stop   = the count's `invalidation_price` — the cardinal-rule "trap door"
                 (the wave-2 low for a wave-3 trade); the level that *voids* the
                 count, exactly the EW trader's hard stop.
      - target = the engine's first Fibonacci target (e.g. ~1.618×W1 for wave 3),
                 or a reward:risk fallback when no fib target is published.
      - confidence = the engine's calibrated confidence → drives conviction sizing.

    This is the structural, per-name entry EXP-15/16 pointed to: position for the
    wave-3 thrust near the wave-2 low with a tight count-based stop and a far fib
    target — a reward:risk profile a 20-day-high breakout can't match — instead of
    chasing strength. Debounced per (symbol, direction) so each new motive leg
    triggers once. The engine enforces the three cardinal rules; we trade only its
    surviving primary count, with its invalidation as the risk backbone.
    """

    name = "elliott_wave"

    def __init__(
        self,
        *,
        pivot_period: int = 5,
        entry_waves=("3",),
        min_confidence: float = 0.0,
        lookback: int = 300,
        reward_risk_mult: float = 2.0,
        side: str = "both",
    ) -> None:
        self.pivot_period = pivot_period
        self.entry_waves = tuple(str(w) for w in entry_waves)
        self.min_confidence = min_confidence
        self.lookback = lookback
        self.reward_risk_mult = reward_risk_mult
        if side not in ("long", "short", "both"):
            raise ValueError(f"side must be long|short|both, got {side!r}")
        self.side = side
        self._last_wave: dict = {}
        self._engine = None

    def setup(self, ctx: Context) -> None:
        self._last_wave = {}
        self._engine = None

    def on_bar(self, ctx: Context) -> Optional[Signal]:
        from app.indicators.pivots import PivotDetector
        from app.signals.elliott.engine import WaveEngine

        df = ctx.history.to_dataframe()
        if len(df) < max(40, self.pivot_period * 5):
            return None
        sub = df.iloc[-self.lookback:]
        close, high, low = sub["close"], sub["high"], sub["low"]
        pivots = PivotDetector(period=self.pivot_period, source="hl").detect(close, high, low)
        if len(pivots) < 5:
            return None
        if self._engine is None:
            self._engine = WaveEngine()
        labeling = self._engine.label(
            pivots, last_price=float(close.iloc[-1]), symbol=ctx.bar.symbol,
            interval=getattr(ctx, "_exec_interval", "1d"),
            as_of_index=len(close) - 1, as_of=ctx.bar.timestamp,
        )
        prim = labeling.primary
        if prim is None or prim.current_wave is None:
            return None
        direction = "long" if prim.direction == "up" else "short"
        if self.side != "both" and self.side != direction:
            return None

        cw = prim.current_wave
        key = (ctx.bar.symbol, direction)
        prev = self._last_wave.get(key)
        self._last_wave[key] = cw
        if cw not in self.entry_waves or prev == cw:
            return None  # not a motive-leg onset (or already signaled this leg)
        if labeling.confidence < self.min_confidence:
            return None

        entry = float(close.iloc[-1])
        scen = labeling.scenarios[0] if labeling.scenarios else None
        stop = float(scen.invalidation) if scen is not None else float(prim.invalidation_price)
        target = float(scen.next_target) if (scen is not None and scen.next_target is not None) else None
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        if target is None:
            target = entry + self.reward_risk_mult * risk * (1.0 if direction == "long" else -1.0)
        # Geometry sanity: long → stop < entry < target; short → target < entry < stop.
        if direction == "long" and not (stop < entry < target):
            return None
        if direction == "short" and not (target < entry < stop):
            return None
        conf = min(1.0, max(0.0, labeling.confidence))
        return Signal(
            symbol=ctx.bar.symbol, direction=direction, entry=entry, stop=stop,
            target_1=target, confidence=conf, kind=f"ew_wave{cw}",
            rationale=(f"EW {prim.structure} {prim.direction} wave {cw} conf {conf:.2f} "
                       f"inval {stop:.2f} tgt {target:.2f}"),
        )


_SOURCES = {
    "ma_cross": MACrossSignalSource,
    "breakout": BreakoutSignalSource,
    "divergence": DivergenceSignalSource,
    "elliott_wave": ElliottWaveSource,
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


def _pivot_lows(values, k: int) -> list[int]:
    """Positions of strict pivot lows: a bar lower than the `k` bars on each
    side. The last `k` bars can't be pivots (need k confirming bars after) —
    that lag is what keeps divergence detection no-look-ahead."""
    n = len(values)
    out: list[int] = []
    for i in range(k, n - k):
        v = values[i]
        if v != v:  # NaN
            continue
        if all(v < values[j] for j in range(i - k, i)) and all(
            v < values[j] for j in range(i + 1, i + k + 1)
        ):
            out.append(i)
    return out


def _pivot_highs(values, k: int) -> list[int]:
    """Positions of strict pivot highs (mirror of _pivot_lows): a bar higher
    than the `k` bars on each side. Last `k` bars can't be pivots (no-look-ahead)."""
    n = len(values)
    out: list[int] = []
    for i in range(k, n - k):
        v = values[i]
        if v != v:
            continue
        if all(v > values[j] for j in range(i - k, i)) and all(
            v > values[j] for j in range(i + 1, i + k + 1)
        ):
            out.append(i)
    return out


def _last_two(series: pd.Series) -> tuple[float, float]:
    if len(series) < 2:
        return (float("nan"), float("nan"))
    return (float(series.iloc[-1]), float(series.iloc[-2]))


def _isnan(v: float) -> bool:
    return v != v
