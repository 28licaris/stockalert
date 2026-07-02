"""
Composable A+ filters (Milestone 2).

A `SignalFilter` answers one yes/no question about a candidate `Signal`
(is it in an uptrend? is the reward:risk good enough? is volume expanding?).
Filters are pure (Context + Signal only), individually testable, and stack:
`FilteredSignalSource` wraps any base `SignalSource` and only re-emits a signal
when the filter set passes — either ALL filters (`mode="all"`) or a weighted
SCORE clearing a threshold (`mode="score"`).

This is the surface where "A+" is *defined and iterated*, not hardcoded: a
config (or an AI agent) declares `filters: [{name, params}, ...]`, and because
each filter is a separate unit we can A/B its contribution with the sweep tool.
The signal's `confidence` is overwritten with the normalized filter score so the
strategy/ranking layer can sort A+ setups.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Protocol, runtime_checkable

import pandas as pd

from app.services.sim.context import Context
from app.services.sim.signal_source import BaseSignalSource, Signal, SignalSource


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    score: float          # 0..1 contribution (weighted by the filter's weight)
    reason: str = ""


@runtime_checkable
class SignalFilter(Protocol):
    name: str
    weight: float

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult: ...


class BaseFilter:
    name: str = "base"
    weight: float = 1.0

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:  # pragma: no cover
        return FilterResult(passed=True, score=self.weight)


# ─────────────────────────────────────────────────────────────────────
# Concrete filters (pure)
# ─────────────────────────────────────────────────────────────────────


class TrendFilter(BaseFilter):
    """Long setups must be above a rising-trend MA (close > MA(period))."""

    name = "trend"

    def __init__(self, *, ma: str = "sma", period: int = 50, weight: float = 1.0) -> None:
        self.ma = ma
        self.period = period
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        series = ctx.indicator(self.ma, period=self.period)
        val = float(series.iloc[-1]) if len(series) else float("nan")
        if val != val:  # NaN (warmup) — fail closed
            return FilterResult(False, 0.0, f"{self.ma}{self.period} warmup")
        # Long wants price above trend, short wants price below — confirm direction.
        ok = signal.entry > val if signal.direction == "long" else signal.entry < val
        return FilterResult(
            ok, self.weight if ok else 0.0,
            f"close vs {self.ma}{self.period}={val:.2f} ({signal.direction})",
        )


class MinRewardRiskFilter(BaseFilter):
    """Reward:risk floor (target_1 vs stop)."""

    name = "reward_risk"

    def __init__(self, *, min_rr: float = 1.5, weight: float = 1.0) -> None:
        self.min_rr = min_rr
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        ok = signal.reward_risk >= self.min_rr
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"rr={signal.reward_risk:.2f} {'>=' if ok else '<'} {self.min_rr}")


class VolumeExpansionFilter(BaseFilter):
    """Current bar volume must exceed `mult` × trailing average (a real move)."""

    name = "volume"

    def __init__(self, *, mult: float = 1.5, period: int = 20, weight: float = 1.0) -> None:
        self.mult = mult
        self.period = period
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        df = ctx.history.to_dataframe()
        if "volume" not in df or len(df) < self.period + 1:
            return FilterResult(False, 0.0, "volume warmup")
        vol_now = float(df["volume"].iloc[-1])
        vol_avg = float(df["volume"].iloc[-(self.period + 1):-1].mean())
        ok = vol_avg > 0 and vol_now >= self.mult * vol_avg
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"vol {vol_now:.0f} vs {self.mult}×avg {vol_avg:.0f}")


class RegimeFilter(BaseFilter):
    """Only trade when the market benchmark is in an uptrend (above its SMA).

    Reads `ctx.market` (a MarketContext the engine loads when `benchmark` is set
    in the config). Fails closed if no benchmark is loaded or it's still warming
    up — a market filter with no market data should not pass.
    """

    name = "regime"

    def __init__(self, *, ma_period: int = 50, weight: float = 1.0) -> None:
        self.ma_period = ma_period
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        mc = getattr(ctx, "market", None)
        if mc is None:
            return FilterResult(False, 0.0, "no benchmark loaded (set config.benchmark)")
        above = mc.above_ma_asof(ctx.bar.timestamp, self.ma_period)
        if above is None:
            return FilterResult(False, 0.0, f"{mc.benchmark} regime warmup")
        # Long confirms in an up-regime, short in a down-regime — trade WITH the regime.
        ok = above if signal.direction == "long" else (not above)
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"{mc.benchmark} {'up' if above else 'down'}-regime ({signal.direction})")


class RelativeStrengthFilter(BaseFilter):
    """Only trade names outperforming the benchmark over `lookback` bars."""

    name = "relative_strength"

    def __init__(self, *, lookback: int = 60, weight: float = 1.0) -> None:
        self.lookback = lookback
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        mc = getattr(ctx, "market", None)
        if mc is None:
            return FilterResult(False, 0.0, "no benchmark loaded (set config.benchmark)")
        bench_ret = mc.return_over_asof(ctx.bar.timestamp, self.lookback)
        df = ctx.history.to_dataframe()
        if bench_ret is None or len(df) < self.lookback + 1:
            return FilterResult(False, 0.0, "RS warmup")
        prev = float(df["close"].iloc[-1 - self.lookback])
        sym_ret = (float(df["close"].iloc[-1]) / prev - 1.0) if prev else 0.0
        # Long wants out-performance, short wants under-performance.
        ok = sym_ret > bench_ret if signal.direction == "long" else sym_ret < bench_ret
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"RS {sym_ret:+.1%} vs {mc.benchmark} {bench_ret:+.1%} ({signal.direction})")


class RsiBullFilter(BaseFilter):
    """Bullish momentum confirmation: RSI above a threshold (default 50)."""

    name = "rsi_bull"

    def __init__(self, *, period: int = 14, threshold: float = 50.0, weight: float = 1.0) -> None:
        self.period = period
        self.threshold = threshold
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        s = ctx.indicator("rsi", period=self.period)
        v = float(s.iloc[-1]) if len(s) else float("nan")
        if v != v:
            return FilterResult(False, 0.0, "rsi warmup")
        # Momentum confirms direction: long wants RSI above the threshold, short
        # wants it below the mirror (100 - threshold).
        ok = v > self.threshold if signal.direction == "long" else v < (100.0 - self.threshold)
        return FilterResult(ok, self.weight if ok else 0.0, f"rsi {v:.0f} ({signal.direction})")


class MacdBullFilter(BaseFilter):
    """Bullish confirmation: MACD line above zero (uptrend momentum)."""

    name = "macd_bull"

    def __init__(self, *, weight: float = 1.0) -> None:
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        s = ctx.indicator("macd")  # canonical MACD output is the MACD line
        v = float(s.iloc[-1]) if len(s) else float("nan")
        if v != v:
            return FilterResult(False, 0.0, "macd warmup")
        # Long confirms with MACD above zero, short with MACD below zero.
        ok = v > 0.0 if signal.direction == "long" else v < 0.0
        return FilterResult(ok, self.weight if ok else 0.0, f"macd {v:+.2f} ({signal.direction})")


class AdxStrengthFilter(BaseFilter):
    """Trend-strength gate: ADX ≥ threshold (default 20). Direction-agnostic —
    ADX measures how strongly a name is trending, not which way. Pros skip
    trend-following entries when ADX is low (chop)."""

    name = "adx"

    def __init__(self, *, period: int = 14, threshold: float = 20.0, weight: float = 1.0) -> None:
        self.period = period
        self.threshold = threshold
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        s = ctx.indicator("adx", period=self.period)
        v = float(s.iloc[-1]) if len(s) else float("nan")
        if v != v:
            return FilterResult(False, 0.0, "adx warmup")
        ok = v >= self.threshold
        return FilterResult(ok, self.weight if ok else 0.0, f"adx {v:.0f} {'>=' if ok else '<'} {self.threshold:.0f}")


class AtrVolatilityFilter(BaseFilter):
    """Volatility-regime band: ATR as a fraction of price must sit in
    [min_pct, max_pct]. Skips dead names (no range to work with) and too-wild
    names (stops get blown out). Direction-agnostic."""

    name = "atr_volatility"

    def __init__(self, *, period: int = 14, min_pct: float = 0.01, max_pct: float = 0.08,
                 weight: float = 1.0) -> None:
        self.period = period
        self.min_pct = min_pct
        self.max_pct = max_pct
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        s = ctx.indicator("atr", period=self.period)
        atr = float(s.iloc[-1]) if len(s) else float("nan")
        price = float(ctx.bar.close)
        if atr != atr or price <= 0:
            return FilterResult(False, 0.0, "atr warmup")
        pct = atr / price
        ok = self.min_pct <= pct <= self.max_pct
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"atr% {pct:.1%} in [{self.min_pct:.1%},{self.max_pct:.1%}]")


class HtfTrendFilter(BaseFilter):
    """Higher-timeframe alignment: resample the entry-interval history to weekly
    and require the current price on the trend side of the weekly SMA. Daily
    entries only fire when the WEEKLY trend agrees — the classic pro filter that
    keeps you from fighting the bigger tide. Direction-aware."""

    name = "htf_trend"

    def __init__(self, *, weeks: int = 20, rule: str = "W-FRI", weight: float = 1.0) -> None:
        self.weeks = weeks
        self.rule = rule
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        df = ctx.history.to_dataframe()
        if df.empty or not isinstance(df.index, pd.DatetimeIndex):
            return FilterResult(False, 0.0, "htf warmup")
        weekly = df["close"].resample(self.rule).last().dropna()
        if len(weekly) < self.weeks:
            return FilterResult(False, 0.0, f"htf warmup ({len(weekly)}/{self.weeks}w)")
        ma = float(weekly.iloc[-self.weeks:].mean())
        price = float(ctx.bar.close)
        ok = price > ma if signal.direction == "long" else price < ma
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"weekly SMA{self.weeks}={ma:.2f} ({signal.direction})")


class NotExtendedFilter(BaseFilter):
    """Don't chase: price must be within `max_atr` ATRs of its MA on the trade
    side. Filters out entries late in a move where reward:risk is poor.
    Direction-aware."""

    name = "not_extended"

    def __init__(self, *, ma: str = "sma", period: int = 20, atr_period: int = 14,
                 max_atr: float = 4.0, weight: float = 1.0) -> None:
        self.ma = ma
        self.period = period
        self.atr_period = atr_period
        self.max_atr = max_atr
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        ma_s = ctx.indicator(self.ma, period=self.period)
        atr_s = ctx.indicator("atr", period=self.atr_period)
        ma = float(ma_s.iloc[-1]) if len(ma_s) else float("nan")
        atr = float(atr_s.iloc[-1]) if len(atr_s) else float("nan")
        if ma != ma or atr != atr or atr <= 0:
            return FilterResult(False, 0.0, "not_extended warmup")
        price = float(ctx.bar.close)
        # Distance above MA (long) / below MA (short), in ATR units.
        dist = (price - ma) / atr if signal.direction == "long" else (ma - price) / atr
        ok = dist <= self.max_atr
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"{dist:.1f} ATR from {self.ma}{self.period} (<= {self.max_atr})")


class RelativeVolumeFilter(BaseFilter):
    """Participation gate: current volume ≥ `mult` × its trailing average over a
    longer window (default 50). Distinct from `volume` (a short 20-bar spike) —
    this confirms sustained institutional interest. Direction-agnostic."""

    name = "rel_volume"

    def __init__(self, *, period: int = 50, mult: float = 1.2, weight: float = 1.0) -> None:
        self.period = period
        self.mult = mult
        self.weight = weight

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        df = ctx.history.to_dataframe()
        if "volume" not in df or len(df) < self.period + 1:
            return FilterResult(False, 0.0, "rel_volume warmup")
        vol_now = float(df["volume"].iloc[-1])
        vol_avg = float(df["volume"].iloc[-(self.period + 1):-1].mean())
        if vol_avg <= 0:
            return FilterResult(False, 0.0, "rel_volume no-avg")
        rvol = vol_now / vol_avg
        ok = rvol >= self.mult
        return FilterResult(ok, self.weight if ok else 0.0, f"rvol {rvol:.2f} (>= {self.mult})")


class EwtImpulseFilter(BaseFilter):
    """Elliott Wave gate — "trade the wave."

    Only pass a setup when the name's wave structure (labeled as-of the current
    bar — no look-ahead) is in a MOTIVE/impulse wave in the trade direction
    (default waves 3 & 5, the trend legs) with engine confidence ≥ threshold.
    Rejects entries during corrections (waves 2/4) and counter-trend counts.

    Per-name and structural — the right kind of gate (EXP-15: top-down market
    gates hurt; bottom-up structure gates fit momentum). Uses the pure, no-look-
    ahead `app.signals.elliott` engine over recent pivots; runs only on bars where
    the base source already fired, so cost is bounded.
    """

    name = "ewt_impulse"

    def __init__(self, *, pivot_period: int = 5, allowed_waves: tuple = ("3", "5"),
                 min_confidence: float = 0.30, lookback: int = 250, weight: float = 1.0) -> None:
        self.pivot_period = pivot_period
        self.allowed_waves = tuple(str(w) for w in allowed_waves)
        self.min_confidence = min_confidence
        self.lookback = lookback
        self.weight = weight
        self._engine = None

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        from app.indicators.pivots import PivotDetector
        from app.signals.elliott.engine import WaveEngine

        df = ctx.history.to_dataframe()
        if len(df) < max(30, self.pivot_period * 4):
            return FilterResult(False, 0.0, "ewt warmup")
        sub = df.iloc[-self.lookback:]
        close, high, low = sub["close"], sub["high"], sub["low"]
        pivots = PivotDetector(period=self.pivot_period, source="hl").detect(close, high, low)
        if len(pivots) < 4:
            return FilterResult(False, 0.0, "ewt: too few swings")
        if self._engine is None:
            self._engine = WaveEngine()
        labeling = self._engine.label(
            pivots, last_price=float(close.iloc[-1]), symbol=ctx.bar.symbol,
            interval=getattr(ctx, "_exec_interval", "1d"),
            as_of_index=len(close) - 1, as_of=ctx.bar.timestamp,
        )
        cw = labeling.current_wave
        prim = labeling.primary
        if cw is None or prim is None:
            return FilterResult(False, 0.0, "ewt: no count")
        ewt_dir = "long" if prim.direction == "up" else "short"
        ok = (
            cw in self.allowed_waves
            and ewt_dir == signal.direction
            and labeling.confidence >= self.min_confidence
        )
        return FilterResult(ok, self.weight if ok else 0.0,
                            f"wave {cw} {prim.direction} conf {labeling.confidence:.2f} ({signal.direction})")


class MetaRankFilter(BaseFilter):
    """Layer-2 learned gate: score the setup with the trained probability ranker
    (P of target-before-stop) and pass only high-P trades. Features are computed by
    the SHARED `app.services.sim.ranker` code — the same function used to build the
    training set — so train/inference parity is guaranteed by construction. The
    signal's confidence becomes P, so conviction sizing scales with probability."""

    name = "meta_rank"

    def __init__(self, *, model_path: str = "data/ranker.json", min_proba: float = 0.5,
                 rel_lookback: int = 60, regime_ma: int = 50, weight: float = 1.0) -> None:
        self.model_path = model_path
        self.min_proba = min_proba
        self.rel_lookback = rel_lookback
        self.regime_ma = regime_ma
        self.weight = weight
        self._model = None
        self._tried = False

    def evaluate(self, ctx: Context, signal: Signal) -> FilterResult:
        from app.services.sim.ranker import (
            SYMBOL_FEATURES, compute_symbol_features, load_ranker, predict_proba,
        )
        if not self._tried:
            self._model = load_ranker(self.model_path)
            self._tried = True
        if self._model is None:
            return FilterResult(False, 0.0, "ranker model missing")
        df = ctx.history.to_dataframe()
        if len(df) < 210:
            return FilterResult(False, 0.0, "ranker warmup")
        row = compute_symbol_features(df).iloc[-1]
        if row[SYMBOL_FEATURES].isna().any():
            return FilterResult(False, 0.0, "ranker features NaN")
        mc = getattr(ctx, "market", None)
        spy_ret = mc.return_over_asof(ctx.bar.timestamp, self.rel_lookback) if mc else None
        regime = mc.above_ma_asof(ctx.bar.timestamp, self.regime_ma) if mc else None
        feats = {f: float(row[f]) for f in SYMBOL_FEATURES}
        feats["rel_str"] = float(row["ret60"] - spy_ret) if spy_ret is not None else 0.0
        feats["regime_up"] = 1.0 if regime else 0.0
        p = predict_proba(self._model, feats)
        ok = p >= self.min_proba
        # score → FilteredSignalSource sets confidence (conviction by probability).
        # Calibrate against the TRAIN-set predicted-P distribution when the model
        # carries its quantiles (p10→0, p90→1), so conviction sizing spans the
        # full risk ramp; raw P (~0.15-0.45) would barely engage it. Train-only
        # constants — no holdout leakage.
        conf = p
        p10, p90 = self._model.get("train_p10"), self._model.get("train_p90")
        if p10 is not None and p90 is not None and p90 > p10:
            conf = min(1.0, max(0.0, (p - p10) / (p90 - p10)))
        return FilterResult(ok, conf if ok else 0.0, f"P(win)={p:.2f}")


_FILTERS = {
    "trend": TrendFilter,
    "reward_risk": MinRewardRiskFilter,
    "volume": VolumeExpansionFilter,
    "regime": RegimeFilter,
    "relative_strength": RelativeStrengthFilter,
    "rsi_bull": RsiBullFilter,
    "macd_bull": MacdBullFilter,
    "adx": AdxStrengthFilter,
    "atr_volatility": AtrVolatilityFilter,
    "htf_trend": HtfTrendFilter,
    "not_extended": NotExtendedFilter,
    "rel_volume": RelativeVolumeFilter,
    "ewt_impulse": EwtImpulseFilter,
    "meta_rank": MetaRankFilter,
}


def build_filter(name: str, **params) -> SignalFilter:
    try:
        cls = _FILTERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown filter {name!r}. One of: {', '.join(sorted(_FILTERS))}."
        ) from exc
    return cls(**params)


def list_filters() -> list[str]:
    return sorted(_FILTERS)


# ─────────────────────────────────────────────────────────────────────
# FilteredSignalSource — the A+ gate wrapping any base source
# ─────────────────────────────────────────────────────────────────────


class FilteredSignalSource(BaseSignalSource):
    """
    Wrap a base SignalSource with a stack of filters.

    mode="all"   → emit only if every filter passes.
    mode="score" → emit if the summed filter score ≥ `min_score` (out of the
                   total filter weight); the signal's confidence is set to the
                   normalized score so A+ setups can be ranked.
    """

    name = "filtered"

    def __init__(
        self,
        base: SignalSource,
        filters: list[SignalFilter],
        *,
        mode: str = "all",
        min_score: Optional[float] = None,
    ) -> None:
        if mode not in ("all", "score"):
            raise ValueError(f"mode must be 'all' or 'score', got {mode!r}")
        self.base = base
        self.filters = filters
        self.mode = mode
        self._total_weight = sum(f.weight for f in filters) or 1.0
        self.min_score = min_score if min_score is not None else self._total_weight
        self.name = f"filtered({base.name})"

    def setup(self, ctx: Context) -> None:
        self.base.setup(ctx)

    def on_bar(self, ctx: Context) -> Optional[Signal]:
        sig = self.base.on_bar(ctx)
        if sig is None:
            return None
        if not self.filters:
            return sig
        results = [f.evaluate(ctx, sig) for f in self.filters]
        score = sum(r.score for r in results)
        if self.mode == "all":
            if not all(r.passed for r in results):
                return None
        else:  # "score"
            if score < self.min_score:
                return None
        # Annotate confidence with the normalized A+ score + which filters passed.
        passed = [f.name for f, r in zip(self.filters, results) if r.passed]
        return replace(
            sig,
            confidence=min(1.0, score / self._total_weight),
            rationale=f"{sig.rationale} | A+[{','.join(passed)}]",
        )
