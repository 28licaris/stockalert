"""RRG math — pure functions over daily close series. No I/O, no magic
literals (all windows are passed in; defaults live in `config.py`).

Our flavor of the JdK RS-Ratio / RS-Momentum, defined plainly:

    rel(t)         = close_group(t) / close_benchmark(t)
    rs_ratio(t)    = 100 * rel(t) / SMA(rel, ratio_window)(t)
    rs_momentum(t) = 100 * rs_ratio(t) / SMA(rs_ratio, mom_window)(t)

Both axes are centered at 100 (= in line with the benchmark). The four
quadrants follow from which side of 100 each axis sits on (see `classify`).

Every function is total: insufficient history yields a typed
`SectorScore(sufficient=False, reason=…)`, never NaN on the wire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

from app.services.sectors.schemas import Quadrant, RotationPoint


@dataclass
class SectorScore:
    """Result of scoring one group against the benchmark."""

    sufficient: bool
    reason: Optional[str] = None
    current: Optional[RotationPoint] = None
    tail: list[RotationPoint] = field(default_factory=list)
    relative_strength: list[tuple[date, float]] = field(default_factory=list)


def classify(rs_ratio: float, rs_momentum: float) -> Quadrant:
    """Map the two axes to an RRG quadrant. The benchmark line (100) is
    inclusive on the leading/strong side, so an exact 100/100 reads as
    'leading' — a single, documented convention for the boundary."""
    strong = rs_ratio >= 100.0
    rising = rs_momentum >= 100.0
    if strong and rising:
        return "leading"
    if strong and not rising:
        return "weakening"
    if not strong and rising:
        return "improving"
    return "lagging"


def relative_strength(group_close: pd.Series, bench_close: pd.Series) -> pd.Series:
    """Aligned relative-strength line `group / benchmark` on shared dates."""
    aligned = pd.concat(
        {"g": group_close, "b": bench_close}, axis=1, join="inner"
    ).dropna()
    if aligned.empty:
        return pd.Series(dtype="float64")
    return (aligned["g"] / aligned["b"]).rename("rel")


def _rrg_frame(
    rel: pd.Series, *, ratio_window: int, mom_window: int
) -> pd.DataFrame:
    """RS-Ratio + RS-Momentum frame from a relative-strength line.

    Returns rows only where both axes are warm (SMAs filled), so the
    caller never sees NaN.
    """
    ratio = 100.0 * rel / rel.rolling(ratio_window).mean()
    momentum = 100.0 * ratio / ratio.rolling(mom_window).mean()
    frame = pd.DataFrame({"rs_ratio": ratio, "rs_momentum": momentum}).dropna()
    return frame


def _weekly_points(frame: pd.DataFrame, tail_weeks: int) -> list[RotationPoint]:
    """Last `tail_weeks` weekly samples (last session of each week)."""
    if frame.empty:
        return []
    dt = frame.copy()
    dt.index = pd.to_datetime(dt.index)
    weekly = dt.resample("W-FRI").last().dropna()
    weekly = weekly.tail(tail_weeks)
    return [
        RotationPoint(
            date=ts.date(),
            rs_ratio=float(row.rs_ratio),
            rs_momentum=float(row.rs_momentum),
            quadrant=classify(float(row.rs_ratio), float(row.rs_momentum)),
        )
        for ts, row in weekly.iterrows()
    ]


def score(
    group_close: pd.Series,
    bench_close: pd.Series,
    *,
    ratio_window: int,
    mom_window: int,
    tail_weeks: int,
) -> SectorScore:
    """Score a group against the benchmark. Total: returns an
    `unsufficient` result rather than raising on thin data."""
    rel = relative_strength(group_close, bench_close)
    if rel.empty:
        return SectorScore(False, reason="no overlapping dates with benchmark")

    frame = _rrg_frame(rel, ratio_window=ratio_window, mom_window=mom_window)
    if frame.empty:
        need = ratio_window + mom_window
        return SectorScore(
            False,
            reason=f"insufficient history: need ~{need} sessions, have {len(rel)}",
        )

    last_ts = frame.index[-1]
    last = frame.iloc[-1]
    current = RotationPoint(
        date=pd.Timestamp(last_ts).date(),
        rs_ratio=float(last.rs_ratio),
        rs_momentum=float(last.rs_momentum),
        quadrant=classify(float(last.rs_ratio), float(last.rs_momentum)),
    )

    tail = _weekly_points(frame, tail_weeks)

    # Relative-strength trend line, rebased to 100 at the start of the
    # displayed (warm) window so >100 reads "outperformed benchmark since".
    rel_window = rel.loc[frame.index[0]:]
    base = rel_window.iloc[0]
    rs_line = [
        (pd.Timestamp(idx).date(), float(val / base * 100.0))
        for idx, val in rel_window.items()
    ]

    return SectorScore(
        sufficient=True,
        current=current,
        tail=tail,
        relative_strength=rs_line,
    )
