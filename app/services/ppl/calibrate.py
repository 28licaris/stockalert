"""PIT calibration — the only honest test of whether a fan means anything.

A fan chart is a *distributional* forecast, so it cannot be scored by how close
a point was. The test is the **probability integral transform**: for each
historical anchor, ask what fraction of the simulated terminal prices came in
below the price that actually printed.

    u_i = F_i(y_i)          F_i = predictive CDF at anchor i, y_i = realized price

If the predictive distribution is right, `u ~ Uniform(0, 1)` — a theorem, not a
heuristic (Rosenblatt 1952; Dawid 1984's "probabilistic calibration"). Every
statistic below is a way of asking whether that uniformity holds.

Reading the failure modes:

* `u` piles up near 0 and 1  → the fan is **too narrow**; reality lands outside
  it more often than claimed.
* `u` piles up in the middle → the fan is **too wide**; the model is hedging.
* `u` skews to one side      → the fan is **biased**; drift is wrong.

The harness deliberately calls `PplService.build_from_frame`, the same entry
point the API uses. A calibration harness that reimplements the model validates
the reimplementation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from app.services.ppl.schemas import PplCalibration, PplParams

logger = logging.getLogger(__name__)

# Anchors closer together than the horizon share outcome bars, so their PIT
# values are correlated and n would overstate the evidence. Stride defaults to
# the horizon for that reason.
DEFAULT_QUANTILES: tuple[float, ...] = (0.15, 0.35, 0.65, 0.85)

# Bars of history required before the first anchor — the fit needs enough
# support for a 32-state drift model and an EWMA that has converged.
DEFAULT_MIN_HISTORY = 800

# Coverage tolerance from the spec (§6.2). Binding gate.
COVERAGE_TOLERANCE = 0.05


@dataclass(frozen=True)
class CoverageBand:
    """Realized vs nominal coverage at one quantile, with its noise floor."""

    quantile: float
    realized: float
    n: int

    @property
    def error(self) -> float:
        return self.realized - self.quantile

    @property
    def standard_error(self) -> float:
        """Binomial SE of the realized fraction under the null that the model
        is calibrated. Without this, ±5pp is a number with no scale — at n=50
        it is well inside noise; at n=2000 it is a gross miss."""
        if self.n <= 0:
            return float("nan")
        return float(np.sqrt(self.quantile * (1.0 - self.quantile) / self.n))

    @property
    def z(self) -> float:
        se = self.standard_error
        return float("nan") if not se else self.error / se

    @property
    def within_tolerance(self) -> bool:
        return abs(self.error) <= COVERAGE_TOLERANCE


@dataclass
class PitResult:
    """Everything a calibration run learned about one (symbol, interval)."""

    symbol: str
    interval: str
    horizon_bars: int
    pit_values: np.ndarray
    anchors: list[datetime]
    bands: list[CoverageBand]
    ks_stat: float
    ks_p: float
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    stride: int
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def n_effective(self) -> int:
        return int(self.pit_values.size)

    @property
    def passed(self) -> bool:
        """The binding gate: every quantile within ±5pp (spec §6.2). The KS
        statistic is reported but not binding — at large n it rejects on
        deviations too small to matter."""
        return bool(self.bands) and all(b.within_tolerance for b in self.bands)

    @property
    def mean_pit(self) -> float:
        """0.5 when unbiased. Away from 0.5 means directional bias."""
        return float(self.pit_values.mean()) if self.n_effective else float("nan")

    def to_schema(self) -> PplCalibration:
        return PplCalibration(
            window_start=self.window_start or datetime.now(timezone.utc),
            window_end=self.window_end or datetime.now(timezone.utc),
            n_effective=self.n_effective,
            coverage={f"{b.quantile:.2f}": b.realized for b in self.bands},
            ks_stat=self.ks_stat,
            ks_p=self.ks_p,
            computed_at=datetime.now(timezone.utc),
        )

    def summary(self) -> str:
        head = (
            f"{self.symbol} {self.interval} H={self.horizon_bars} "
            f"n_eff={self.n_effective} (stride={self.stride}) "
            f"mean_PIT={self.mean_pit:.3f} KS={self.ks_stat:.3f} (p={self.ks_p:.3f})"
        )
        lines = [head]
        for b in self.bands:
            flag = "ok " if b.within_tolerance else "MISS"
            lines.append(
                f"  q{b.quantile:.2f}: realized {b.realized * 100:5.1f}%  "
                f"err {b.error * 100:+5.1f}pp  z={b.z:+5.2f}  [{flag}]"
            )
        if self.skipped:
            lines.append(f"  skipped anchors: {self.skipped}")
        lines.append(f"  VERDICT: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def run_pit(
    service,
    symbol: str,
    frame: pd.DataFrame,
    params: Optional[PplParams] = None,
    *,
    stride: Optional[int] = None,
    min_history: int = DEFAULT_MIN_HISTORY,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
    max_anchors: Optional[int] = None,
) -> PitResult:
    """Walk historical anchors and collect the PIT series.

    `frame` is the full OHLC history; each anchor refits on **only** the prefix
    (which `build_from_frame` then truncates to `fit_lookback_bars`, exactly as
    the live path does — see the note there on why that truncation belongs to
    the model rather than to the loader)
    up to that bar, so nothing downstream of the anchor can leak in. That is the
    whole point — a harness that fits once on all history and replays would
    report excellent coverage for a model that cannot be run live.
    """
    params = params or PplParams()
    horizon = params.horizon_bars
    stride = stride or horizon
    if stride < 1:
        raise ValueError(f"stride must be >= 1, got {stride}")

    # PIT must read the FULL predictive CDF. Estimating F(realized) from the
    # thinned render subset adds Monte Carlo noise to every u, which widens the
    # PIT distribution and makes an over-narrow fan look better than it is.
    params = params.model_copy(update={"include_terminal_sample": True})

    closes = frame["close"].to_numpy(dtype="float64")
    last_anchor = len(frame) - horizon - 1
    if last_anchor < min_history:
        raise ValueError(
            f"{symbol}: need > {min_history + horizon} bars to run PIT, "
            f"have {len(frame)}"
        )

    anchor_idx = list(range(min_history, last_anchor + 1, stride))
    if max_anchors is not None and len(anchor_idx) > max_anchors:
        # Thin evenly rather than truncating — a truncated run silently
        # calibrates on the oldest slice of history only.
        keep = np.linspace(0, len(anchor_idx) - 1, max_anchors).round().astype(int)
        dropped = len(anchor_idx) - len(set(keep))
        anchor_idx = [anchor_idx[i] for i in sorted(set(keep))]
        logger.info(
            "ppl calibrate: %s thinned to %d anchors (dropped %d, evenly spaced)",
            symbol, len(anchor_idx), dropped,
        )

    pits: list[float] = []
    anchors: list[datetime] = []
    skipped: dict[str, int] = {}

    for i in anchor_idx:
        prefix = frame.iloc[: i + 1]
        result = service.build_from_frame(symbol, prefix, params)
        if not result.ok or result.snapshot is None:
            skipped[result.status] = skipped.get(result.status, 0) + 1
            continue

        sample = result.snapshot.terminal_sample
        if sample is None:
            raise RuntimeError(
                "PIT requires the full terminal sample; the service returned a "
                "snapshot without one. Did include_terminal_sample get dropped?"
            )
        terminal = np.asarray(sample, dtype="float64")
        realized = closes[i + horizon]
        pits.append(float((terminal < realized).mean()))
        anchors.append(frame.index[i].to_pydatetime())

    pit_values = np.asarray(pits, dtype="float64")
    bands = [
        CoverageBand(
            quantile=float(q),
            realized=float((pit_values < q).mean()) if pit_values.size else float("nan"),
            n=int(pit_values.size),
        )
        for q in quantiles
    ]
    ks_stat, ks_p = ks_uniform(pit_values)

    if skipped:
        logger.warning(
            "ppl calibrate: %s %s skipped %s anchor(s) — %s",
            symbol, params.interval, sum(skipped.values()), skipped,
        )

    return PitResult(
        symbol=symbol,
        interval=params.interval,
        horizon_bars=horizon,
        pit_values=pit_values,
        anchors=anchors,
        bands=bands,
        ks_stat=ks_stat,
        ks_p=ks_p,
        window_start=anchors[0] if anchors else None,
        window_end=anchors[-1] if anchors else None,
        stride=stride,
        skipped=skipped,
    )


def ks_uniform(u: np.ndarray) -> tuple[float, float]:
    """Kolmogorov-Smirnov statistic against Uniform(0,1), plus its p-value.

    Implemented here rather than pulled from scipy (spec D8): the statistic is
    four lines, and the asymptotic p-value is the standard Kolmogorov series

        Q(t) = 2 * sum_{k>=1} (-1)^(k-1) exp(-2 k^2 t^2),   t = (sqrt(n) + 0.12
                                                                 + 0.11/sqrt(n)) * D

    (Stephens 1970's small-sample correction, as used by Numerical Recipes.)

    Reported, never binding — see `PitResult.passed`. At large n, KS rejects on
    deviations far too small to matter for a trading display.
    """
    u = np.asarray(u, dtype="float64")
    u = u[np.isfinite(u)]
    n = u.size
    if n < 2:
        return float("nan"), float("nan")

    x = np.sort(u)
    i = np.arange(1, n + 1)
    d_plus = float(np.max(i / n - x))
    d_minus = float(np.max(x - (i - 1) / n))
    d = max(d_plus, d_minus)

    t = (np.sqrt(n) + 0.12 + 0.11 / np.sqrt(n)) * d
    if t <= 0:
        return d, 1.0
    k = np.arange(1, 101)
    p = 2.0 * np.sum(((-1.0) ** (k - 1)) * np.exp(-2.0 * k**2 * t**2))
    return d, float(min(max(p, 0.0), 1.0))


def split_by_volatility_regime(
    result: PitResult, frame: pd.DataFrame, *, lookback: int = 60
) -> dict[str, list[CoverageBand]]:
    """Coverage broken out by trailing-volatility tercile at the anchor.

    A model can hit its coverage on average by being too wide in calm regimes
    and too narrow in violent ones — the two errors cancel in the pooled number
    and leave the fan wrong exactly when it matters. This repo reports
    regime-split results as a matter of habit; here it is load-bearing.
    """
    if result.n_effective == 0:
        return {}

    log_ret = np.log(frame["close"] / frame["close"].shift(1))
    trailing = log_ret.rolling(lookback).std()

    vols = np.asarray(
        [trailing.get(pd.Timestamp(a), np.nan) for a in result.anchors], dtype="float64"
    )
    usable = np.isfinite(vols)
    if usable.sum() < 30:
        logger.warning(
            "ppl calibrate: only %d anchors have trailing vol — skipping regime split",
            int(usable.sum()),
        )
        return {}

    lo, hi = np.quantile(vols[usable], [1 / 3, 2 / 3])
    buckets = {
        "calm": usable & (vols <= lo),
        "normal": usable & (vols > lo) & (vols <= hi),
        "volatile": usable & (vols > hi),
    }

    out: dict[str, list[CoverageBand]] = {}
    for name, mask in buckets.items():
        subset = result.pit_values[mask]
        if subset.size == 0:
            continue
        out[name] = [
            CoverageBand(
                quantile=b.quantile,
                realized=float((subset < b.quantile).mean()),
                n=int(subset.size),
            )
            for b in result.bands
        ]
    return out
