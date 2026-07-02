"""
DT-1 — day-trading setup detectors + honest intraday trade simulation.

Pure functions over ONE symbol-day's 1-minute regular-session bars (numpy
arrays). No look-ahead: a trigger at bar i uses bars ≤ i only; the outcome is
resolved on bars > i. One trigger per setup per symbol-day (position-event
semantics — the first tradeable opportunity is the only one a live account
gets, EXP-36 lesson).

Setups (a-priori definitions, locked before any results were seen):
  orb            Opening-range (first 15m) breakout in the GAP direction.
                 Stop = other side of the range. Skip if risk > 3%.
  vwap_reclaim   ≥15 of the last 30 bars on the far side of session VWAP,
                 then a 1m close crossing it. Long reclaim / short reject.
                 Stop = extreme of the last 15 bars.
  first_pullback Opening drive ≥ +2% above the open into a new high inside
                 30m, first pullback retracing ≥ 30% of the drive, entry on a
                 close above the pullback's high. Stop = pullback low.
                 (Long-only by construction; mirror deferred.)
  flush_reclaim  New session low after 10:00 on ≥3× (20m avg) volume, then a
                 close back above the flush bar's high within 10 bars.
                 Stop = flush low. Long-only reversal.

Execution model (pre-registered): entries fill at the trigger bar's CLOSE
plus slippage; slippage = max($0.01, 0.25 × trigger-bar range) × slip_mult —
report every result at slip_mult 1 AND 2. Stops fill at the stop level minus
the same slippage (worst-case ordering inside a 1m bar); targets are resting
limits AT the level (2R); everything force-closes at 15:55. R is normalized
by (entry − stop).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

TARGET_R = 2.0
ORB_MINUTES = 15
MAX_ORB_RISK_PCT = 0.03
EOD_MINUTE = 385  # 15:55 ET, minutes after 09:30


@dataclass(frozen=True)
class Trigger:
    setup: str
    side: str            # "long" | "short"
    i: int               # trigger bar index (entry decision at this bar's close)
    entry: float         # raw trigger close (slippage applied in simulate)
    stop: float


@dataclass(frozen=True)
class TradeResult:
    setup: str
    side: str
    i: int
    entry_fill: float
    stop: float
    exit_fill: float
    exit_i: int
    exit_reason: str     # "stop" | "target" | "eod"
    r_mult: float
    hold_minutes: int


def session_vwap(h, l, c, v) -> np.ndarray:
    tp = (h + l + c) / 3.0
    cv = np.cumsum(v)
    return np.divide(np.cumsum(tp * v), np.where(cv > 0, cv, 1.0))


def _first(cond: np.ndarray, start: int) -> Optional[int]:
    idx = np.nonzero(cond[start:])[0]
    return int(idx[0]) + start if len(idx) else None


def detect_orb(o, h, l, c, v, gap_pct: float) -> Optional[Trigger]:
    n = len(c)
    if n <= ORB_MINUTES + 1 or gap_pct == 0:
        return None
    or_hi, or_lo = h[:ORB_MINUTES].max(), l[:ORB_MINUTES].min()
    if or_lo <= 0:
        return None
    long_side = gap_pct > 0
    lvl, stop = (or_hi, or_lo) if long_side else (or_lo, or_hi)
    if abs(lvl - stop) / lvl > MAX_ORB_RISK_PCT:
        return None
    cond = c > or_hi if long_side else c < or_lo
    i = _first(cond, ORB_MINUTES)
    if i is None or i >= EOD_MINUTE - 5:
        return None
    return Trigger("orb", "long" if long_side else "short", i, float(c[i]), float(stop))


def detect_vwap_reclaim(o, h, l, c, v) -> Optional[Trigger]:
    n = len(c)
    if n < 45:
        return None
    vw = session_vwap(h, l, c, v)
    below = c < vw
    for i in range(30, min(n, EOD_MINUTE - 5)):
        window = below[i - 30:i]
        if below[i - 1] and not below[i] and window.sum() >= 15:      # reclaim
            stop = float(l[i - 15:i + 1].min())
            if stop < c[i]:
                return Trigger("vwap_reclaim", "long", i, float(c[i]), stop)
        if (~window).sum() >= 15 and not below[i - 1] and below[i]:   # reject
            stop = float(h[i - 15:i + 1].max())
            if stop > c[i]:
                return Trigger("vwap_reclaim", "short", i, float(c[i]), stop)
    return None


def detect_first_pullback(o, h, l, c, v) -> Optional[Trigger]:
    n = len(c)
    if n < 40:
        return None
    open_px = o[0]
    if open_px <= 0:
        return None
    # opening drive: new session high ≥ +2% above the open, inside 30 minutes
    drive_end = None
    for i in range(1, min(30, n)):
        if h[i] >= h[:i + 1].max() and (h[i] - open_px) / open_px >= 0.02:
            drive_end = i
    if drive_end is None:
        return None
    drive_hi = float(h[drive_end])
    drive = drive_hi - open_px
    # first pullback: retrace ≥ 30% of the drive without a new high
    pb_lo, pb_hi, pb_start = None, None, None
    for i in range(drive_end + 1, min(n, EOD_MINUTE - 5)):
        if h[i] > drive_hi:
            if pb_lo is None:
                return None            # new high before any pullback formed
            break
        if pb_lo is None or l[i] < pb_lo:
            pb_lo, pb_start = float(l[i]), (pb_start or i)
        if pb_lo is not None and (drive_hi - pb_lo) >= 0.30 * drive:
            pb_hi = float(h[pb_start:i + 1].max())
            # entry: first close above the pullback's high
            j = _first(c > pb_hi, i + 1)
            if j is None or j >= EOD_MINUTE - 5:
                return None
            if pb_lo < c[j]:
                return Trigger("first_pullback", "long", j, float(c[j]), pb_lo)
            return None
    return None


def detect_flush_reclaim(o, h, l, c, v) -> Optional[Trigger]:
    n = len(c)
    if n < 45:
        return None
    vol_avg = np.convolve(v, np.ones(20) / 20.0, mode="full")[:n]
    for f in range(30, min(n, EOD_MINUTE - 15)):
        is_new_low = l[f] <= l[:f + 1].min()
        if not is_new_low or vol_avg[f - 1] <= 0 or v[f] < 3.0 * vol_avg[f - 1]:
            continue
        j = _first(c > h[f], f + 1)
        if j is not None and j <= f + 10 and j < EOD_MINUTE - 5 and l[f] < c[j]:
            return Trigger("flush_reclaim", "long", j, float(c[j]), float(l[f]))
    return None


def simulate(trig: Trigger, o, h, l, c, slip_mult: float = 1.0) -> Optional[TradeResult]:
    """Resolve a trigger on bars AFTER trig.i. Worst-case ordering inside a
    1m bar (stop before target). Entries/stops pay slippage; targets are
    resting limits at the level; 15:55 force-close at the bar close."""
    n = len(c)
    i = trig.i
    rng = max(float(h[i] - l[i]), 0.0)
    slip = max(0.01, 0.25 * rng) * slip_mult
    is_long = trig.side == "long"
    entry = trig.entry + slip if is_long else trig.entry - slip
    risk = (entry - trig.stop) if is_long else (trig.stop - entry)
    if risk <= 0:
        return None
    target = entry + TARGET_R * risk if is_long else entry - TARGET_R * risk
    end = min(n - 1, EOD_MINUTE)
    for j in range(i + 1, end + 1):
        stop_hit = l[j] <= trig.stop if is_long else h[j] >= trig.stop
        if stop_hit:
            fill = (trig.stop - slip) if is_long else (trig.stop + slip)
            # a bar that OPENS through the stop fills at its (worse) open
            if is_long and o[j] < trig.stop:
                fill = float(o[j]) - slip
            if not is_long and o[j] > trig.stop:
                fill = float(o[j]) + slip
            r = ((fill - entry) if is_long else (entry - fill)) / risk
            return TradeResult(trig.setup, trig.side, i, entry, trig.stop,
                               fill, j, "stop", float(r), j - i)
        target_hit = h[j] >= target if is_long else l[j] <= target
        if target_hit:
            r = TARGET_R
            return TradeResult(trig.setup, trig.side, i, entry, trig.stop,
                               float(target), j, "target", float(r), j - i)
    fill = float(c[end])
    r = ((fill - entry) if is_long else (entry - fill)) / risk
    return TradeResult(trig.setup, trig.side, i, entry, trig.stop,
                       fill, end, "eod", float(r), end - i)


DETECTORS = {
    "orb": lambda o, h, l, c, v, gap: detect_orb(o, h, l, c, v, gap),
    "vwap_reclaim": lambda o, h, l, c, v, gap: detect_vwap_reclaim(o, h, l, c, v),
    "first_pullback": lambda o, h, l, c, v, gap: detect_first_pullback(o, h, l, c, v),
    "flush_reclaim": lambda o, h, l, c, v, gap: detect_flush_reclaim(o, h, l, c, v),
}


def run_symbol_day(o, h, l, c, v, gap_pct: float, slip_mult: float = 1.0):
    """First trigger per setup, resolved. Returns list[TradeResult]."""
    out = []
    for name, det in DETECTORS.items():
        trig = det(o, h, l, c, v, gap_pct)
        if trig is not None:
            res = simulate(trig, o, h, l, c, slip_mult)
            if res is not None:
                out.append(res)
    return out
