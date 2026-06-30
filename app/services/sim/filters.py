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
        ok = signal.entry > val
        return FilterResult(
            ok, self.weight if ok else 0.0,
            f"close {'>' if ok else '<='} {self.ma}{self.period}={val:.2f}",
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


_FILTERS = {
    "trend": TrendFilter,
    "reward_risk": MinRewardRiskFilter,
    "volume": VolumeExpansionFilter,
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
