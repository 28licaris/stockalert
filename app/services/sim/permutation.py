"""
Bar-permutation kernel for Monte Carlo Permutation Testing (MCPT).

Method (Masters-style bar permutation, as popularized by
neurotrader888/mcpt): decompose each bar into log-space *relative*
components —

  gap   r_o = log(open_t)  - log(close_{t-1})
  body  r_h = log(high_t)  - log(open_t)
        r_l = log(low_t)   - log(open_t)
        r_c = log(close_t) - log(open_t)

— shuffle the gaps and the bodies as two independent permutations, then
recompose prices from an anchor bar forward. The permuted series keeps
the exact multiset of gaps and bar bodies (so mean/variance/skew/
kurtosis of returns, OHLC validity, and the terminal price are all
preserved) while destroying temporal structure. Any profit a strategy
extracts from a permutation is data-mining luck; the fraction of
permutations that beat the real result is the MCPT p-value
(see `significance.py`).

Multi-market: all symbols are permuted with ONE master shuffle over the
union trading calendar, so cross-sectional co-movement is preserved and
cross-sectional strategies (momentum top-N) face an honest null.
Symbols with partial coverage (IPO / delisting) get their shuffle by
restricting the master permutation to the master-calendar positions
they cover — symbols with identical coverage share the identical
shuffle, and overlapping symbols keep a consistent relative ordering on
the overlap. Volume travels with the bar body.

Walk-forward use: pass `start_after` — bars at or before it stay real
(the optimizer's initial train window sees true history), bars after it
are permuted. This mirrors `start_index` in the reference
implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from app.services.sim.schemas import Bar

_OHLC = ("open", "high", "low", "close")


@dataclass(frozen=True)
class PermutedBar:
    """Concrete Bar (satisfies the sim `Bar` Protocol) for permuted data."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def permute_frames(
    frames: Mapping[str, pd.DataFrame],
    *,
    seed: int,
    start_after: Optional[datetime] = None,
) -> dict[str, pd.DataFrame]:
    """
    Permute per-symbol OHLC(V) DataFrames with one shared master shuffle.

    Each frame must have a strictly-increasing unique DatetimeIndex and
    columns open/high/low/close (volume optional; carried with the bar
    body when present, else emitted as 0.0). Prices must be positive and
    finite — violations raise ValueError (no silent repair).

    Returns new frames (same index per symbol, ohlcv columns only).
    Bars with timestamp <= start_after are returned unchanged.
    """
    if not frames:
        raise ValueError("permute_frames: no symbols supplied")

    symbols = list(frames)
    for sym in symbols:
        _validate_frame(sym, frames[sym])

    # Master calendar = union of all timestamps, chronological.
    master = pd.DatetimeIndex(
        sorted(set().union(*(frames[s].index for s in symbols)))
    )
    n = len(master)

    # Global permutable region G: master positions strictly after start_after
    # (position 0 is never permutable — the very first bar anchors rebuilds).
    if start_after is not None:
        start_pos = int(master.searchsorted(start_after, side="right")) - 1
    else:
        start_pos = 0
    permutable = np.arange(max(start_pos, 0) + 1, n)
    if len(permutable) < 2:
        raise ValueError(
            f"permute_frames: only {len(permutable)} permutable master slots "
            f"after start_after={start_after} — nothing to shuffle"
        )

    rng = np.random.default_rng(seed)
    body_perm = rng.permutation(permutable)  # h/l/c relatives + volume
    gap_perm = rng.permutation(permutable)   # close→open gaps

    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        out[sym] = _permute_one(sym, frames[sym], master, start_pos, body_perm, gap_perm)
    return out


def permute_bar_lists(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    seed: int,
    start_after: Optional[datetime] = None,
) -> dict[str, list[PermutedBar]]:
    """
    Adapter for the backtester's bar shape: dict[symbol -> list[Bar]] in,
    dict[symbol -> list[PermutedBar]] out. Empty symbol lists pass through
    empty (the backtester already tolerates missing symbols).
    """
    frames: dict[str, pd.DataFrame] = {}
    for sym, bars in bars_by_symbol.items():
        if not bars:
            continue
        frames[sym] = pd.DataFrame(
            {
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
            },
            index=pd.DatetimeIndex([b.timestamp for b in bars]),
        )
    permuted = permute_frames(frames, seed=seed, start_after=start_after)
    out: dict[str, list[PermutedBar]] = {s: [] for s in bars_by_symbol}
    for sym, df in permuted.items():
        # zip over columns (not iterrows) — this runs millions of times per MCPT.
        timestamps = (b.timestamp for b in bars_by_symbol[sym])
        out[sym] = [
            PermutedBar(sym, ts, o, h, l, c, v)
            for ts, o, h, l, c, v in zip(
                timestamps,
                df["open"].tolist(),
                df["high"].tolist(),
                df["low"].tolist(),
                df["close"].tolist(),
                df["volume"].tolist(),
            )
        ]
    return out


# ─────────────────────────────────────────────────────────────────────
# internals
# ─────────────────────────────────────────────────────────────────────


def _validate_frame(sym: str, df: pd.DataFrame) -> None:
    missing = [c for c in _OHLC if c not in df.columns]
    if missing:
        raise ValueError(f"permute_frames: {sym} missing columns {missing}")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError(f"permute_frames: {sym} index must be strictly increasing/unique")
    vals = df[list(_OHLC)].to_numpy(dtype=float)
    n_bad = int((~np.isfinite(vals)).sum() + (vals <= 0).sum())
    if n_bad:
        raise ValueError(
            f"permute_frames: {sym} has {n_bad} non-positive/non-finite OHLC "
            "values — clean the input; log-space permutation cannot proceed"
        )


def _restrict(master_perm: np.ndarray, allowed: np.ndarray, n_master: int) -> np.ndarray:
    """
    Restrict a master-slot permutation to a subset of slots.

    Returns the elements of `master_perm` that fall in `allowed`, in
    master-perm order — a bijection on `allowed` that is identical across
    symbols sharing the same coverage and order-consistent on overlaps.
    """
    mask = np.zeros(n_master, dtype=bool)
    mask[allowed] = True
    return master_perm[mask[master_perm]]


def _permute_one(
    sym: str,
    df: pd.DataFrame,
    master: pd.DatetimeIndex,
    start_pos: int,
    body_perm: np.ndarray,
    gap_perm: np.ndarray,
) -> pd.DataFrame:
    cov = master.searchsorted(df.index)          # master positions covered
    k = len(cov)
    has_vol = "volume" in df.columns
    vol = df["volume"].to_numpy(dtype=float) if has_vol else np.zeros(k)

    log_px = np.log(df[list(_OHLC)].to_numpy(dtype=float))
    lo, lh, ll, lc = (log_px[:, i] for i in range(4))

    # Anchor = last covered slot at/before start_pos, else the first bar.
    # Everything after the anchor (local) is this symbol's permutable set.
    anchor_loc = int(np.searchsorted(cov, start_pos, side="right")) - 1
    if anchor_loc < 0:
        anchor_loc = 0
    if k - (anchor_loc + 1) < 2:
        # 0 or 1 permutable bars — permutation is the identity; return real.
        return _emit(sym, df, lo, lh, ll, lc, vol)

    p_s = cov[anchor_loc + 1:]                   # master slots to permute

    # Relative components (local, full length).
    r_o = np.empty(k)
    r_o[0] = np.nan
    r_o[1:] = lo[1:] - lc[:-1]
    r_h, r_l, r_c = lh - lo, ll - lo, lc - lo

    # Source order from the master shuffles, mapped to local indices.
    body_src = np.searchsorted(cov, _restrict(body_perm, p_s, len(master)))
    gap_src = np.searchsorted(cov, _restrict(gap_perm, p_s, len(master)))

    tgt = np.arange(anchor_loc + 1, k)
    r_o2, r_h2, r_l2, r_c2, vol2 = r_o.copy(), r_h.copy(), r_l.copy(), r_c.copy(), vol.copy()
    r_o2[tgt] = r_o[gap_src]
    r_h2[tgt] = r_h[body_src]
    r_l2[tgt] = r_l[body_src]
    r_c2[tgt] = r_c[body_src]
    vol2[tgt] = vol[body_src]

    # Recompose from the anchor forward: close walks by gap+body, open/high/low
    # hang off each bar's close/open.
    lo2, lh2, ll2, lc2 = lo.copy(), lh.copy(), ll.copy(), lc.copy()
    steps = r_o2[tgt] + r_c2[tgt]
    lc2[tgt] = lc[anchor_loc] + np.cumsum(steps)
    lo2[tgt] = lc2[tgt] - r_c2[tgt]
    lh2[tgt] = lo2[tgt] + r_h2[tgt]
    ll2[tgt] = lo2[tgt] + r_l2[tgt]

    return _emit(sym, df, lo2, lh2, ll2, lc2, vol2)


def _emit(sym, df, lo, lh, ll, lc, vol) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": np.exp(lo),
            "high": np.exp(lh),
            "low": np.exp(ll),
            "close": np.exp(lc),
            "volume": vol,
        },
        index=df.index,
    )
