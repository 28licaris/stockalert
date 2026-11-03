"""Turn a cloud of simulated terminal prices into the four price levels.

Two methods, per the spec:

* **`quantile`** (default) — fixed quantiles of terminal price. This is the
  only method PIT calibration can validate, because "the realized price should
  fall below the 15th percentile 15% of the time" is a statement about
  quantiles specifically. Everything else is decoration.

* **`density`** — kernel-density modes of the terminal cloud, closer to what
  the reference charts appear to draw. Offered for comparison; not the default,
  and explicitly not validatable.

Gaussian KDE and peak-finding are implemented here in numpy rather than pulled
from scipy (spec D8) — both are a few lines, and neither justifies a new
production dependency.
"""
from __future__ import annotations

import numpy as np

from app.services.ppl.schemas import PplLevel

_KDE_GRID_POINTS = 512
_PEAK_PROMINENCE_FRAC = 0.05


def horizon_sigma(terminal: np.ndarray, spot: float) -> float:
    """Standard deviation of the terminal **log** return."""
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")
    return float(np.std(np.log(terminal / spot)))


def min_move_from_sigma(sigma_h: float, mult: float) -> float:
    """The "profit level" filter: the smallest move worth marking, as a fraction.

    Deriving it from the horizon sigma rather than configuring it per symbol is
    what reproduces the reference's scaling across timeframes (1.57% at 30m vs
    3.71% at 2h — a 2.36x that tracks the sqrt-of-horizon widening) without a
    per-symbol constant to maintain.
    """
    return float(sigma_h * mult)


def quantile_levels(
    terminal: np.ndarray,
    spot: float,
    quantiles: tuple[float, float, float, float],
    min_move: float,
) -> list[PplLevel]:
    """Four levels at fixed quantiles of terminal price.

    `side`/`rank` describe position in the distribution: the two lowest
    quantiles are "down", the two straddling the median are "inner".
    """
    qs = sorted(quantiles)
    if len(qs) != 4:
        raise ValueError(f"expected exactly 4 quantiles, got {len(quantiles)}")
    if not all(0.0 < q < 1.0 for q in qs):
        raise ValueError(f"quantiles must lie strictly in (0, 1), got {quantiles}")

    prices = np.quantile(terminal, qs)
    roles: list[tuple[str, str]] = [
        ("down", "outer"),
        ("down", "inner"),
        ("up", "inner"),
        ("up", "outer"),
    ]

    levels: list[PplLevel] = []
    for q, price, (side, rank) in zip(qs, prices, roles):
        levels.append(_build_level(float(price), spot, min_move, side, rank, quantile=q))
    return levels


def density_levels(
    terminal: np.ndarray,
    spot: float,
    min_move: float,
    *,
    max_levels: int = 4,
) -> list[PplLevel]:
    """Modes of the terminal-price density, nearest-to-spot first.

    Returns however many modes clear the prominence bar, up to `max_levels` —
    a unimodal cloud honestly yields one level, and padding it out to four
    would be inventing structure.
    """
    grid = np.linspace(float(terminal.min()), float(terminal.max()), _KDE_GRID_POINTS)
    dens = gaussian_kde_grid(terminal, grid)
    peaks = find_peaks_simple(dens, prominence=float(dens.max()) * _PEAK_PROMINENCE_FRAC)
    if peaks.size == 0:
        return []

    candidates = sorted(grid[peaks], key=lambda p: abs(p / spot - 1.0))[:max_levels]

    levels: list[PplLevel] = []
    for price in candidates:
        side = "up" if price >= spot else "down"
        rank = "inner" if sum(1 for lv in levels if lv.side == side) == 0 else "outer"
        levels.append(_build_level(float(price), spot, min_move, side, rank, quantile=None))
    return levels


def _build_level(
    price: float,
    spot: float,
    min_move: float,
    side: str,
    rank: str,
    *,
    quantile: float | None,
) -> PplLevel:
    pct = price / spot - 1.0
    too_close = abs(pct) < min_move
    return PplLevel(
        price=price,
        quantile=quantile,
        side=side,                     # type: ignore[arg-type]
        rank=rank,                     # type: ignore[arg-type]
        pct_from_spot=pct,
        suppressed=too_close,
        # Suppressed levels are returned and flagged, never dropped — a filtered
        # result is still a reported outcome (docs/standards/coding.md).
        suppressed_reason=(
            f"within min_move ({abs(pct):.4f} < {min_move:.4f})" if too_close else None
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Small numeric helpers — in-repo replacements for scipy (spec D8)
# ─────────────────────────────────────────────────────────────────────


def gaussian_kde_grid(
    samples: np.ndarray, grid: np.ndarray, bandwidth: float | None = None
) -> np.ndarray:
    """Gaussian KDE evaluated on `grid`.

    Bandwidth defaults to Silverman's rule of thumb, using the smaller of the
    standard deviation and the IQR-derived scale so a fat tail doesn't
    over-smooth the body.
    """
    x = np.asarray(samples, dtype="float64")
    x = x[np.isfinite(x)]
    if x.size < 2:
        raise ValueError(f"need at least 2 finite samples for a KDE, got {x.size}")

    if bandwidth is None:
        sd = float(np.std(x))
        iqr = float(np.subtract(*np.percentile(x, [75, 25])))
        scale = min(sd, iqr / 1.349) if iqr > 0 else sd
        if scale <= 0:
            scale = sd if sd > 0 else 1.0
        bandwidth = 0.9 * scale * x.size ** (-1 / 5)
    if bandwidth <= 0:
        raise ValueError(f"bandwidth must be positive, got {bandwidth}")

    z = (grid[:, None] - x[None, :]) / bandwidth
    return np.exp(-0.5 * z**2).sum(axis=1) / (x.size * bandwidth * np.sqrt(2 * np.pi))


def find_peaks_simple(y: np.ndarray, prominence: float = 0.0) -> np.ndarray:
    """Indices of local maxima at least `prominence` above their surroundings.

    Prominence is measured against the higher of the two adjacent valleys — the
    standard definition, and the reason a shoulder on a big peak doesn't count
    as its own mode.
    """
    y = np.asarray(y, dtype="float64")
    if y.size < 3:
        return np.empty(0, dtype=int)

    interior = np.arange(1, y.size - 1)
    is_peak = (y[interior] > y[interior - 1]) & (y[interior] >= y[interior + 1])
    peaks = interior[is_peak]
    if prominence <= 0 or peaks.size == 0:
        return peaks

    keep = []
    for p in peaks:
        left_valley = y[: p + 1].min()
        right_valley = y[p:].min()
        if y[p] - max(left_valley, right_valley) >= prominence:
            keep.append(p)
    return np.asarray(keep, dtype=int)
