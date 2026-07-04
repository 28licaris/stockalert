"""
Tier-1 in-sample MCPT: cheap, vectorized signal-family screen.

For a signal family (grid of interpretable params), optimize pooled
profit factor of `signal x next-bar log return` across the universe on
REAL bars, then re-run the SAME optimization on N bar-permuted
universes (shared master shuffle — cross-sectional structure preserved,
see app/services/sim/permutation.py). The MCPT p-value is the fraction
of permutations whose optimized PF matches or beats the real one: the
probability the family's in-sample excellence is data-mining luck.

This is the SCREEN. Anything that passes still needs the full-engine
walk-forward MCPT (scripts/mcpt_walkforward.py) before promotion —
see docs/standards/trading_subsystem.md.

Usage:
  poetry run python scripts/mcpt_insample.py --config configs/dyn_breakout_v2_top50_brake.yaml \
      --family donchian --start 2016-01-01 --end 2021-12-31 --n-perms 1000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from app.services.sim.permutation import noise_frames, permute_frames  # noqa: E402
from app.services.sim.significance import mcpt_pvalue  # noqa: E402

UTC = timezone.utc


# ── signal families (wide matrices: index=date, columns=symbol) ─────


def _donchian(wide: dict[str, pd.DataFrame], lookback: int) -> pd.DataFrame:
    """Long above the prior (lookback-1)-day high, flat below the prior low."""
    close = wide["close"]
    upper = close.rolling(lookback - 1).max().shift(1)
    lower = close.rolling(lookback - 1).min().shift(1)
    sig = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    sig = sig.mask(close > upper, 1.0).mask(close < lower, 0.0)
    return sig.ffill().fillna(0.0)


def _ma_cross(wide: dict[str, pd.DataFrame], fast: int, slow: int) -> pd.DataFrame:
    close = wide["close"]
    return (close.rolling(fast).mean() > close.rolling(slow).mean()).astype(float)


def _breakout_vol(wide: dict[str, pd.DataFrame], lookback: int, vol_mult: float) -> pd.DataFrame:
    """Production-shaped breakout: N-day-high close + volume confirm; donchian exit."""
    close, volume = wide["close"], wide["volume"]
    upper = close.rolling(lookback - 1).max().shift(1)
    lower = close.rolling(lookback - 1).min().shift(1)
    vol_ok = volume >= vol_mult * volume.rolling(20).mean().shift(1)
    sig = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    sig = sig.mask((close > upper) & vol_ok, 1.0).mask(close < lower, 0.0)
    return sig.ffill().fillna(0.0)


def _state(close: pd.DataFrame, enter: pd.DataFrame, exit_: pd.DataFrame) -> pd.DataFrame:
    """Long/flat state machine: 1 on `enter`, 0 on `exit_`, held in between."""
    sig = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    return sig.mask(enter, 1.0).mask(exit_, 0.0).ffill().fillna(0.0)


def _wilder_rsi_wide(close: pd.DataFrame, n: int) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _meanrev_rsi(wide, n: int, entry: float, exit: float) -> pd.DataFrame:
    rsi = _wilder_rsi_wide(wide["close"], n)
    return _state(wide["close"], rsi < entry, rsi > exit)


def _meanrev_zscore(wide, n: int, z: float) -> pd.DataFrame:
    close = wide["close"]
    sma = close.rolling(n).mean()
    zs = (close - sma) / close.rolling(n).std()
    return _state(close, zs < -z, zs >= 0)


def _xsec_momentum(wide, lookback, bucket: float, rebalance: int = 21) -> pd.DataFrame:
    """Long the top cross-sectional bucket by trailing return, 21-bar rebalance."""
    close = wide["close"]
    if lookback == "12-1":
        mom = close.shift(21) / close.shift(252) - 1.0
    else:
        mom = close / close.shift(int(lookback)) - 1.0
    rb = mom.iloc[::rebalance]
    thresh = rb.quantile(bucket, axis=1)
    member = rb.ge(thresh, axis=0) & rb.notna()
    return member.reindex(close.index).ffill().fillna(False).astype(float)


def _vol_compression(wide, atr_pctile: float, breakout: int) -> pd.DataFrame:
    """ATR-percentile squeeze arms the symbol; range break enters, range low exits."""
    close, high, low = wide["close"], wide["high"], wide["low"]
    prev_c = close.shift(1)
    tr = np.maximum.reduce([
        (high - low).to_numpy(), (high - prev_c).abs().to_numpy(),
        (low - prev_c).abs().to_numpy()])
    tr = pd.DataFrame(tr, index=close.index, columns=close.columns)
    atr_pct = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / close
    armed = atr_pct.rolling(252).rank(pct=True) <= atr_pctile
    hh = close.rolling(breakout).max().shift(1)
    ll = close.rolling(breakout).min().shift(1)
    return _state(close, armed & (close > hh), close < ll)


def _gap(wide, direction: str, gap: float, hold: int) -> pd.DataFrame:
    """Overnight gap trigger (enter at trigger close), fixed `hold`-bar exposure."""
    open_, close = wide["open"], wide["close"]
    g = open_ / close.shift(1) - 1.0
    trigger = ((g > gap) if direction == "follow" else (g < -gap)).astype(float)
    return trigger.rolling(hold, min_periods=1).max().fillna(0.0)


def _high_52wk(wide, prox: float, pullback: int) -> pd.DataFrame:
    """Pullback low while within `prox` of the 52-wk high; exit at 2x prox distance."""
    close = wide["close"]
    hi52 = close.rolling(252).max()
    near = close >= hi52 * (1 - prox)
    at_pullback_low = close <= close.rolling(pullback).min()
    return _state(close, near & at_pullback_low, close < hi52 * (1 - 2 * prox))


def _overnight_condition(wide, condition: str) -> pd.DataFrame:
    """Hold close->open ONLY (gap-return family), conditioned on the prior day."""
    r = np.log(wide["close"]).diff()
    masks = {
        "down_day": r < 0, "up_day": r > 0,
        "down_1pct": r < -0.01, "up_1pct": r > 0.01,
    }
    return masks[condition].astype(float)


def _xsec_reversal(wide, lookback: int, bucket: float) -> pd.DataFrame:
    """Long the cross-sectional BOTTOM bucket by trailing return; hold = lookback."""
    close = wide["close"]
    mom = close / close.shift(lookback) - 1.0
    rb = mom.iloc[::lookback]
    thresh = rb.quantile(bucket, axis=1)
    member = rb.le(thresh, axis=0) & rb.notna()
    return member.reindex(close.index).ffill().fillna(False).astype(float)


def _lag1_reversal(wide, threshold: float) -> pd.DataFrame:
    """Long for one bar after a down day (simplest reversion formulation)."""
    r = np.log(wide["close"]).diff()
    return (r < threshold).astype(float)


def _volume_capitulation(wide, down: float, vol_mult: float, hold: int) -> pd.DataFrame:
    """Down day on climactic volume = forced-seller exhaustion; fixed hold."""
    r = np.log(wide["close"]).diff()
    volume = wide["volume"]
    vol_avg = volume.rolling(20).mean().shift(1)
    trigger = ((r < -down) & (volume >= vol_mult * vol_avg)).astype(float)
    return trigger.rolling(hold, min_periods=1).max().fillna(0.0)


def _breadth(wide, ma: int) -> pd.Series:
    close = wide["close"]
    above = (close > close.rolling(ma).mean())
    return above.sum(axis=1) / close.notna().sum(axis=1).clip(lower=1)


def _breadth_timing(wide, rule: str, ma: int) -> pd.DataFrame:
    """Participation regime times broad exposure (signal broadcast to all names)."""
    b = _breadth(wide, ma)
    if rule == "level_0.5":
        sig = (b > 0.5).astype(float)
    elif rule == "level_0.6":
        sig = (b > 0.6).astype(float)
    else:  # thrust: <0.4 -> >0.6 within 10 days, hold 20
        trigger = ((b > 0.6) & (b.rolling(10).min() < 0.4)).astype(float)
        sig = trigger.rolling(20, min_periods=1).max().fillna(0.0)
    close = wide["close"]
    return pd.DataFrame(
        np.repeat(sig.to_numpy()[:, None], close.shape[1], axis=1),
        index=close.index, columns=close.columns,
    )


def _market_relative_reversion(wide, n: int, entry: float) -> pd.DataFrame:
    """RSI on the stock-minus-SPY log spread: idiosyncratic panic, market removed."""
    close = wide["close"]
    if "SPY" not in close.columns:
        raise SystemExit("market_relative_reversion requires SPY in the universe")
    spread = np.log(close).sub(np.log(close["SPY"]), axis=0)
    rsi = _wilder_rsi_wide(spread, n)
    return _state(close, rsi < entry, rsi > 50)


def _xsec_lowvol_max(wide, metric: str, bucket: float, rebalance: int = 21) -> pd.DataFrame:
    """Monthly bottom-bucket on realized vol or prior-month max daily return."""
    close = wide["close"]
    r = np.log(close).diff()
    m = r.rolling(63).std() if metric == "vol63" else r.rolling(21).max()
    rb = m.iloc[::rebalance]
    thresh = rb.quantile(bucket, axis=1)
    member = rb.le(thresh, axis=0) & rb.notna()
    return member.reindex(close.index).ffill().fillna(False).astype(float)


def _leadlag_spy(wide, spy_thr: float, hold: int) -> pd.DataFrame:
    """Big SPY UP day -> long that day's bottom-quintile laggards, fixed hold."""
    close = wide["close"]
    if "SPY" not in close.columns:
        raise SystemExit("leadlag_spy requires SPY in the universe")
    r = np.log(close).diff()
    big_up = r["SPY"] > spy_thr
    lag_thresh = r.quantile(0.2, axis=1)
    laggard = r.le(lag_thresh, axis=0)
    trigger = laggard.mul(big_up, axis=0).astype(float)
    return trigger.rolling(hold, min_periods=1).max().fillna(0.0)


def _survivor_conditioning(wide, gate: str) -> pd.DataFrame:
    """The H-3 winner (RSI(4)<10 / >50, LOCKED) gated by market regime."""
    close = wide["close"]
    if "SPY" not in close.columns:
        raise SystemExit("survivor_conditioning requires SPY in the universe")
    base = _meanrev_rsi(wide, n=4, entry=10, exit=50)
    spy_r = np.log(close["SPY"]).diff()
    vol20 = spy_r.rolling(20).std()
    calm = vol20 < vol20.rolling(252).median()
    b = _breadth(wide, 200)
    gates = {
        "calm": calm, "stressed": ~calm,
        "breadth_up": b > 0.5, "breadth_down": b <= 0.5,
    }
    g = gates[gate].astype(float)
    return base.mul(g, axis=0)


def _day_positions(idx: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """(position within day, position from day END) for session-bar indexes."""
    days = pd.Series(np.arange(len(idx)), index=idx).groupby(idx.date)
    pos = days.cumcount().to_numpy()
    counts = days.transform("count").to_numpy()
    return pos, counts - 1 - pos


def _intraday_momentum(wide, pred_bars: int, min_move: float) -> pd.DataFrame:
    """H-29: first-hour(s) return predicts the last hour. Signal sits on the
    second-to-last bar of the day (earning that close -> day-close return)
    when the day's first `pred_bars` returns sum above min_move."""
    close = wide["close"]
    r = np.log(close).diff()
    pos, from_end = _day_positions(close.index)
    pred = r.where(pd.Series(pos < pred_bars, index=close.index), 0.0)
    day = pd.Series(close.index.date, index=close.index)
    day_pred = pred.groupby(day.values).transform("sum")
    on_bar = pd.Series(from_end == 1, index=close.index)  # second-to-last bar
    sig = day_pred.gt(min_move).mul(on_bar, axis=0)
    return sig.astype(float)


def _fomc_hourly(wide, entry: str, exit_start: str) -> pd.DataFrame:
    """H-30: hold SPY on announcement day from `entry` through the bar
    STARTING at `exit_start` exclusive (exit before the 2pm decision).
    entry='open' holds intraday bars only; entry='prior_close' also holds
    the prior day's last bar (capturing the overnight gap)."""
    close = wide["close"]
    csv = Path(__file__).resolve().parent / "data" / "fomc_scheduled_meetings.csv"
    ann = set(pd.to_datetime(pd.read_csv(csv, comment="#")["announcement_date"]).dt.date)
    idx = close.index
    is_ann = pd.Series([d in ann for d in idx.date], index=idx)
    hhmm = pd.Series([t.strftime("%H:%M") for t in idx.time], index=idx)
    pos, from_end = _day_positions(idx)
    held = is_ann & (hhmm < exit_start)
    if entry == "prior_close":
        # also hold the bar BEFORE an announcement day's first bar
        nxt_is_ann = np.roll(is_ann.to_numpy(), -1)
        prior_last = (from_end == 0) & nxt_is_ann
        prior_last[-1] = False
        held = held | pd.Series(prior_last, index=idx)
    sig = pd.DataFrame(0.0, index=idx, columns=close.columns)
    sig.loc[held] = 1.0
    return sig


def _tom_last_hour(wide, last_bars: int) -> pd.DataFrame:
    """H-31: the TOM window (locked 5/2 from H-14) concentrated into the
    final `last_bars` hour(s) of each in-window day."""
    close = wide["close"]
    idx = close.index
    period = pd.PeriodIndex(idx, freq="M")
    day = pd.Series(idx.date, index=idx)
    day_rank = day.groupby(period).transform(lambda s: pd.factorize(s)[0])
    days_in_month = day.groupby(period).transform(lambda s: s.nunique())
    in_window = (day_rank < 2) | (day_rank >= days_in_month - 5)
    _, from_end = _day_positions(idx)
    on_bars = pd.Series((from_end >= 1) & (from_end <= last_bars), index=idx)
    sig_col = (in_window & on_bars).astype(float)
    return pd.DataFrame(
        np.repeat(sig_col.to_numpy()[:, None], close.shape[1], axis=1),
        index=idx, columns=close.columns,
    )


def _spike_analog(wide, s: float, w: int, k: int) -> pd.DataFrame:
    """H-27: on a spike day (|1d return| > s x trailing-20d sigma), find the k
    nearest STRICTLY-PRIOR spike-windows (normalized prior-w-day return
    vectors, pooled cross-symbol, expanding history — no look-ahead) and go
    long next bar iff the analogs' mean next-day return was positive.
    The nonparametric superset of named bar patterns."""
    close = wide["close"]
    rmat = np.log(close).to_numpy()
    rmat = np.vstack([np.full((1, rmat.shape[1]), np.nan), np.diff(rmat, axis=0)])
    r = pd.DataFrame(rmat, index=close.index, columns=close.columns)
    sigma = r.rolling(20).std().shift(1)
    z = (r / sigma).to_numpy()
    T, N = rmat.shape
    sig = np.zeros((T, N))

    # Collect triggers in time order with their normalized windows + outcomes.
    trig_by_day: dict[int, list[tuple[int, np.ndarray]]] = {}
    for t in range(w + 21, T):
        for n in np.flatnonzero(np.abs(z[t]) > s):
            win = rmat[t - w:t, n]
            if np.isnan(win).any():
                continue
            sd = win.std()
            if sd <= 0:
                continue
            trig_by_day.setdefault(t, []).append((n, (win - win.mean()) / sd))

    bank_w: list[np.ndarray] = []   # past windows (normalized)
    bank_f: list[float] = []        # each past window's NEXT-day return
    for t in sorted(trig_by_day):
        if len(bank_w) >= k:
            M = np.asarray(bank_w)
            F = np.asarray(bank_f)
            for n, q in trig_by_day[t]:
                d = ((M - q) ** 2).sum(axis=1)
                vote = F[np.argpartition(d, k - 1)[:k]].mean()
                if vote > 0:
                    sig[t, n] = 1.0
        # Append this day's windows AFTER matching (strictly-prior history);
        # their outcome r[t+1] is only ever read by LATER days, so no look-ahead.
        for n, q in trig_by_day[t]:
            if t + 1 < T and not np.isnan(rmat[t + 1, n]):
                bank_w.append(q)
                bank_f.append(rmat[t + 1, n])
    return pd.DataFrame(sig, index=close.index, columns=close.columns)


def _spike_analog_multiday(wide, s: float, w: int, k: int, h: int) -> pd.DataFrame:
    """H-28: spike_analog with h-day vote horizon and h-bar hold. An analog's
    h-day outcome only joins the library once RESOLVED (h days after its
    trigger) — the resolution delay is what keeps multi-day voting causal."""
    close = wide["close"]
    rmat = np.log(close).to_numpy()
    rmat = np.vstack([np.full((1, rmat.shape[1]), np.nan), np.diff(rmat, axis=0)])
    r = pd.DataFrame(rmat, index=close.index, columns=close.columns)
    sigma = r.rolling(20).std().shift(1)
    z = (r / sigma).to_numpy()
    T, N = rmat.shape
    sig = np.zeros((T, N))

    trig_by_day: dict[int, list[tuple[int, np.ndarray]]] = {}
    for t in range(w + 21, T):
        for n in np.flatnonzero(np.abs(z[t]) > s):
            win = rmat[t - w:t, n]
            if np.isnan(win).any():
                continue
            sd = win.std()
            if sd <= 0:
                continue
            trig_by_day.setdefault(t, []).append((n, (win - win.mean()) / sd))

    bank_w: list[np.ndarray] = []
    bank_f: list[float] = []
    pending: list[tuple[int, np.ndarray, float]] = []  # (resolve_day, window, outcome)
    for t in sorted(trig_by_day):
        still = []
        for resolve, q, f in pending:
            if resolve <= t:
                bank_w.append(q)
                bank_f.append(f)
            else:
                still.append((resolve, q, f))
        pending = still
        if len(bank_w) >= k:
            M = np.asarray(bank_w)
            F = np.asarray(bank_f)
            for n, q in trig_by_day[t]:
                d = ((M - q) ** 2).sum(axis=1)
                if F[np.argpartition(d, k - 1)[:k]].mean() > 0:
                    sig[t:t + h, n] = 1.0  # hold h bars
        for n, q in trig_by_day[t]:
            fwd = rmat[t + 1:t + 1 + h, n]
            if len(fwd) == h and not np.isnan(fwd).any():
                pending.append((t + h, q, float(fwd.sum())))
    return pd.DataFrame(sig, index=close.index, columns=close.columns)


def _fomc_drift(wide, k: int) -> pd.DataFrame:
    """Long the k trading days ending at the FOMC announcement close
    (Lucca-Moench pre-announcement drift). Scheduled meetings only, from
    scripts/data/fomc_scheduled_meetings.csv (Fed public records).
    sig[t] earns t -> t+1, so the window is the k bars BEFORE the
    announcement day: positions carry into (and through) the announcement
    close."""
    close = wide["close"]
    csv = Path(__file__).resolve().parent / "data" / "fomc_scheduled_meetings.csv"
    dates = pd.to_datetime(pd.read_csv(csv, comment="#")["announcement_date"]).dt.date
    idx_dates = pd.Series(close.index.date, index=close.index)
    positions = {d: i for i, d in enumerate(idx_dates)}
    mask = np.zeros(len(close), dtype=bool)
    for d in dates:
        # announcement bar = the trading day matching d (skip if outside window)
        i = positions.get(d)
        if i is None:
            continue
        mask[max(i - k, 0):i] = True
    return pd.DataFrame(
        np.repeat(mask[:, None].astype(float), close.shape[1], axis=1),
        index=close.index, columns=close.columns,
    )


def _seasonality_tom(wide, before: int, after: int) -> pd.DataFrame:
    """Long the turn-of-month window: last `before` and first `after` trading days."""
    close = wide["close"]
    idx = close.index
    period = pd.PeriodIndex(idx, freq="M")
    pos = pd.Series(np.arange(len(idx)), index=idx).groupby(period).cumcount()
    counts = pd.Series(pos.to_numpy(), index=idx).groupby(period).transform("count")
    in_window = (pos < after) | (pos >= counts - before)
    return pd.DataFrame(
        np.repeat(in_window.to_numpy()[:, None], close.shape[1], axis=1).astype(float),
        index=idx, columns=close.columns,
    )


FAMILIES = {
    "donchian": [({"lookback": lb}, _donchian) for lb in range(10, 105, 5)],
    "ma_cross": [
        ({"fast": f, "slow": s}, _ma_cross)
        for f, s in ((5, 20), (5, 50), (10, 50), (10, 100), (20, 100), (20, 200), (50, 200))
    ],
    "breakout_vol": [
        ({"lookback": lb, "vol_mult": vm}, _breakout_vol)
        for lb in (10, 15, 20, 30, 40, 60)
        for vm in (1.0, 1.25, 1.5, 2.0)
    ],
    # EXP-39 battery — registered in docs/research_hypotheses.md H-3..H-8
    # BEFORE implementation; grids must match the registry exactly.
    "meanrev_rsi": [
        ({"n": n, "entry": en, "exit": ex}, _meanrev_rsi)
        for n in (2, 3, 4) for en in (10, 15, 20, 25, 30) for ex in (50, 70)
    ],
    "meanrev_zscore": [
        ({"n": n, "z": z}, _meanrev_zscore) for n in (10, 20) for z in (1.5, 2.0, 2.5)
    ],
    "xsec_momentum": [
        ({"lookback": lb, "bucket": b}, _xsec_momentum)
        for lb in (60, 120, "12-1") for b in (0.9, 0.8)
    ],
    "vol_compression": [
        ({"atr_pctile": p, "breakout": bo}, _vol_compression)
        for p in (0.10, 0.20) for bo in (5, 10)
    ],
    "gap": [
        ({"direction": d, "gap": g, "hold": h}, _gap)
        for d in ("follow", "fade") for g in (0.01, 0.02, 0.03) for h in (1, 3, 5)
    ],
    "high_52wk": [
        ({"prox": p, "pullback": pb}, _high_52wk) for p in (0.02, 0.05, 0.10) for pb in (3, 5)
    ],
    # EXP-41 Wave-2 battery — registered as H-11..H-14 BEFORE implementation;
    # grids must match the registry exactly.
    "overnight_condition": [
        ({"condition": c}, _overnight_condition)
        for c in ("down_day", "up_day", "down_1pct", "up_1pct")
    ],
    "xsec_reversal": [
        ({"lookback": lb, "bucket": b}, _xsec_reversal)
        for lb in (5, 10, 21) for b in (0.1, 0.2)
    ],
    "lag1_reversal": [
        ({"threshold": t}, _lag1_reversal) for t in (0.0, -0.01, -0.02)
    ],
    "seasonality_tom": [
        ({"before": b, "after": a}, _seasonality_tom) for b in (3, 5) for a in (2, 3)
    ],
    # EXP-42 Wave-3 battery — registered as H-17..H-22 BEFORE implementation;
    # grids must match the registry exactly.
    "volume_capitulation": [
        ({"down": d, "vol_mult": v, "hold": h}, _volume_capitulation)
        for d in (0.01, 0.02) for v in (3.0, 5.0) for h in (1, 3)
    ],
    "breadth_timing": [
        ({"rule": r, "ma": m}, _breadth_timing)
        for r in ("level_0.5", "level_0.6", "thrust") for m in (100, 200)
    ],
    "market_relative_reversion": [
        ({"n": n, "entry": e}, _market_relative_reversion)
        for n in (2, 4) for e in (10, 15)
    ],
    "xsec_lowvol_max": [
        ({"metric": m, "bucket": b}, _xsec_lowvol_max)
        for m in ("vol63", "max21") for b in (0.1, 0.2)
    ],
    "leadlag_spy": [
        ({"spy_thr": t, "hold": h}, _leadlag_spy) for t in (0.01, 0.02) for h in (1, 3)
    ],
    "survivor_conditioning": [
        ({"gate": g}, _survivor_conditioning)
        for g in ("calm", "stressed", "breadth_up", "breadth_down")
    ],
    # H-15 (Wave-2, unlocked 2026-07-03 by the Fed calendar backfill).
    "fomc_drift": [({"k": k}, _fomc_drift) for k in (1, 2, 3)],
    # EXP-45 H-27: nonparametric analog matching on spike days.
    "spike_analog": [
        ({"s": s, "w": w, "k": k}, _spike_analog)
        for s in (2.5, 3.5) for w in (10, 20) for k in (25, 100)
    ],
    # EXP-45 H-28: multi-day vote horizon (registered before the H-27 verdict).
    "spike_analog_multiday": [
        ({"s": s, "w": w, "k": k, "h": h}, _spike_analog_multiday)
        for s in (2.5, 3.5) for w in (10, 20) for k in (25, 100) for h in (2, 3)
    ],
    # EXP-46 Wave-H (hourly; run with --table ohlcv_hourly --session-aware).
    "intraday_momentum": [
        ({"pred_bars": p, "min_move": m}, _intraday_momentum)
        for p in (1, 2) for m in (0.0, 0.0025)
    ],
    "fomc_hourly": [
        ({"entry": "open", "exit_start": "12:30"}, _fomc_hourly),
        ({"entry": "open", "exit_start": "13:30"}, _fomc_hourly),
        ({"entry": "prior_close", "exit_start": "13:30"}, _fomc_hourly),
    ],
    "tom_last_hour": [({"last_bars": b}, _tom_last_hour) for b in (1, 2)],
}

# Per-family forward-return stream: "cc" = close(t) -> close(t+1) (default);
# "overnight" = close(t) -> open(t+1) (the gap — H-11's claim is specifically
# about the overnight session, and the position exits at the open).
RETURN_KIND: dict[str, str] = {"overnight_condition": "overnight"}


def _fwd_returns(wide: dict[str, pd.DataFrame], kind: str) -> pd.DataFrame:
    if kind == "overnight":
        return np.log(wide["open"]).shift(-1) - np.log(wide["close"])
    return np.log(wide["close"]).diff().shift(-1)


def _pooled_pf(sig: pd.DataFrame, fwd_ret: pd.DataFrame) -> float:
    rets = (sig * fwd_ret).to_numpy().ravel()
    rets = rets[np.isfinite(rets)]
    gains = rets[rets > 0].sum()
    losses = abs(rets[rets < 0].sum())
    return float(gains / losses) if losses > 0 else 0.0


def _wide(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {
        col: pd.DataFrame({s: f[col] for s, f in frames.items()}).sort_index()
        for col in ("open", "high", "low", "close", "volume")
    }


def _optimize(family: str, frames: dict[str, pd.DataFrame]) -> tuple[dict, float]:
    wide = _wide(frames)
    fwd_ret = _fwd_returns(wide, RETURN_KIND.get(family, "cc"))
    best_params, best_pf = {}, 0.0
    for params, fn in FAMILIES[family]:
        pf = _pooled_pf(fn(wide, **params), fwd_ret)
        if pf > best_pf:
            best_params, best_pf = params, pf
    return best_params, best_pf


def _runs_by_column(sig: pd.DataFrame) -> tuple[list[np.ndarray], np.ndarray]:
    """Per-column entry indices + the pooled empirical holding-duration set."""
    a = sig.to_numpy()
    starts_by_col: list[np.ndarray] = []
    durations: list[int] = []
    for j in range(a.shape[1]):
        d = np.diff(np.concatenate([[0.0], a[:, j], [0.0]]))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        starts_by_col.append(starts)
        durations.extend((ends - starts).tolist())
    return starts_by_col, np.asarray(durations, dtype=int)


def _random_exit_signal(
    n_rows: int, starts_by_col: list[np.ndarray], dur_pool: np.ndarray, rng
) -> np.ndarray:
    """Real entries, exits redrawn from the empirical duration distribution."""
    delta = np.zeros((n_rows + 1, len(starts_by_col)))
    for j, starts in enumerate(starts_by_col):
        if len(starts) == 0:
            continue
        durs = rng.choice(dur_pool, size=len(starts))
        ends = np.minimum(starts + durs, n_rows)
        np.add.at(delta[:, j], starts, 1.0)
        np.add.at(delta[:, j], ends, -1.0)
    return (np.cumsum(delta[:-1], axis=0) > 0).astype(float)


# ── data ─────────────────────────────────────────────────────────────


def _load_frames(
    symbols: list[str], start: str, end: str,
    table: str = "ohlcv_daily", align: bool = False,
) -> dict[str, pd.DataFrame]:
    from app.db.client import get_client

    ts_expr = "toDate(timestamp)" if table == "ohlcv_daily" else "timestamp"
    rows = get_client().query(
        f"SELECT symbol, {ts_expr} d, open, high, low, close, volume "
        f"FROM {table} FINAL "
        "WHERE symbol IN %(symbols)s AND toDate(timestamp) BETWEEN %(start)s AND %(end)s "
        "ORDER BY symbol, d",
        parameters={"symbols": symbols, "start": start, "end": end},
    ).result_rows
    df = pd.DataFrame(rows, columns=["symbol", "d", "open", "high", "low", "close", "volume"])
    if df.empty:
        raise SystemExit(f"no ohlcv_daily rows for {len(symbols)} symbols in {start}..{end}")
    df["d"] = pd.to_datetime(df["d"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    frames: dict[str, pd.DataFrame] = {}
    dropped = 0
    for sym, g in df.groupby("symbol"):
        g = g.set_index("d")[["open", "high", "low", "close", "volume"]]
        if len(g) < 30 or (g[["open", "high", "low", "close"]] <= 0).any().any():
            dropped += 1  # too short for any lookback, or unusable prices
            continue
        frames[sym] = g
    print(f"loaded {len(frames)} symbols ({dropped} dropped: <30 bars or bad prices), "
          f"{sum(len(f) for f in frames.values()):,} bars", flush=True)
    if align and frames:
        # Session-aware nulls require a fully aligned universe: inner-join all
        # calendars and report exactly what that discards (no silent trims).
        common = None
        for f in frames.values():
            common = f.index if common is None else common.intersection(f.index)
        before = {s: len(f) for s, f in frames.items()}
        frames = {s: f.loc[common] for s, f in frames.items()}
        trimmed = {s: before[s] - len(common) for s in frames if before[s] != len(common)}
        print(f"aligned to {len(common):,} common bars"
              + (f" (trimmed: {trimmed})" if trimmed else " (no trims)"), flush=True)
    return frames


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", help="portfolio YAML — its `symbols` is the universe")
    ap.add_argument("--symbols", nargs="*", help="explicit universe (overrides --config)")
    ap.add_argument("--family", required=True, choices=sorted(FAMILIES))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--n-perms", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--null", default="permutation",
                    choices=("permutation", "noise", "random_exit"),
                    help="permutation: MCPT (re-optimizes per variant). "
                         "noise: price-jitter fragility of the REAL optimum. "
                         "random_exit: entries kept, exits redrawn (edge locator).")
    ap.add_argument("--scale", type=float, default=0.25, help="noise jitter scale")
    ap.add_argument("--table", default="ohlcv_daily",
                    help="ClickHouse bar table (ohlcv_daily | ohlcv_hourly)")
    ap.add_argument("--session-aware", action="store_true",
                    help="intraday null: shuffle within hour-of-day pools, overnight "
                         "gaps among themselves (requires an aligned universe)")
    ap.add_argument("--bars", default=None,
                    help="bar snapshot (local path or s3://) from research_bars.py; "
                         "when set, no ClickHouse needed — cloud-worker mode")
    ap.add_argument("--out", default=None, help="JSON result path (default data/mcpt/)")
    a = ap.parse_args(argv)

    if a.symbols:
        symbols = a.symbols
    elif a.config:
        symbols = yaml.safe_load(Path(a.config).read_text())["symbols"]
    else:
        raise SystemExit("supply --symbols or --config")

    if a.bars:
        from scripts.research_bars import load_frames as _snapshot_frames
        frames = _snapshot_frames(a.bars, symbols=symbols, start=a.start, end=a.end)
    else:
        frames = _load_frames(symbols, a.start, a.end, table=a.table,
                              align=a.session_aware)
    real_params, real_pf = _optimize(a.family, frames)
    print(f"REAL  {a.family}: best={real_params} pooled_PF={real_pf:.4f}", flush=True)

    if a.null != "permutation":
        if not real_params:
            raise SystemExit("no profitable config on real data — nothing to test")
        best_fn = next(fn for params, fn in FAMILIES[a.family] if params == real_params)
    variant_pfs: list[float] = []
    t0 = time.time()

    if a.null == "random_exit":
        wide = _wide(frames)
        fwd_ret = _fwd_returns(wide, RETURN_KIND.get(a.family, "cc"))
        real_sig = best_fn(wide, **real_params)
        starts_by_col, dur_pool = _runs_by_column(real_sig)
        if len(dur_pool) == 0:
            raise SystemExit("random_exit: the real optimum never enters — nothing to test")
        print(f"  {sum(len(s) for s in starts_by_col):,} entries, "
              f"median hold {int(np.median(dur_pool))} bars", flush=True)
        rng = np.random.default_rng(a.seed)
        for i in range(a.n_perms):
            sig = pd.DataFrame(
                _random_exit_signal(len(real_sig), starts_by_col, dur_pool, rng),
                index=real_sig.index, columns=real_sig.columns)
            variant_pfs.append(_pooled_pf(sig, fwd_ret))
            _progress(a, i, t0, variant_pfs, real_pf)
    else:
        for i in range(a.n_perms):
            if a.null == "noise":
                var = noise_frames(frames, seed=a.seed + i, scale=a.scale)
                w = _wide(var)
                pf = _pooled_pf(best_fn(w, **real_params),
                                _fwd_returns(w, RETURN_KIND.get(a.family, "cc")))
            else:
                perm = permute_frames(frames, seed=a.seed + i,
                                      session_aware=a.session_aware)
                _, pf = _optimize(a.family, perm)
            variant_pfs.append(pf)
            _progress(a, i, t0, variant_pfs, real_pf)

    arr = np.asarray(variant_pfs)
    payload = {
        "kind": f"insample_{a.null}", "family": a.family, "start": a.start,
        "end": a.end, "n_symbols": len(frames), "seed": a.seed,
        "real_params": real_params, "variant_pfs": variant_pfs,
    }
    if a.null == "permutation":
        res = mcpt_pvalue(real_pf, variant_pfs, greater_is_better=True)
        print(f"\nIN-SAMPLE MCPT [{a.family}] {a.start}..{a.end} "
              f"({len(frames)} symbols, {a.n_perms} permutations)")
        print(f"  {res.summary()}")
        payload["result"] = res.model_dump()
    elif a.null == "noise":
        frac_prof = float((arr > 1.0).mean())
        p5 = float(np.percentile(arr, 5))
        print(f"\nNOISE TEST [{a.family}] {a.start}..{a.end} scale={a.scale} "
              f"({a.n_perms} variants, params fixed at real optimum)")
        print(f"  real PF={real_pf:.4f}  noise mean={arr.mean():.4f} sd={arr.std():.4f}"
              f"  p5={p5:.4f}  profitable {frac_prof:.0%}")
        print(f"  verdict: {'ROBUST' if frac_prof >= 0.8 and p5 > 1.0 else 'FRAGILE'}"
              " (heuristic: >=80% variants profitable AND 5th percentile PF > 1)")
        payload["result"] = {"real": real_pf, "mean": float(arr.mean()),
                             "sd": float(arr.std()), "p5": p5,
                             "frac_profitable": frac_prof, "scale": a.scale}
    else:
        frac_asgood = float((arr >= real_pf).mean())
        print(f"\nRANDOM-EXIT DIAGNOSTIC [{a.family}] {a.start}..{a.end} "
              f"({a.n_perms} variants, entries real, exits redrawn)")
        print(f"  real PF={real_pf:.4f}  random-exit mean={arr.mean():.4f} "
              f"sd={arr.std():.4f}  P(random >= real)={frac_asgood:.3f}")
        print("  read: LOW P -> the exit rule carries real value; "
              "HIGH P -> the edge (if any) lives in the entries")
        payload["result"] = {"real": real_pf, "mean": float(arr.mean()),
                             "sd": float(arr.std()), "frac_asgood": frac_asgood}

    out = Path(a.out) if a.out else Path("data/mcpt") / (
        f"insample_{a.null}_{a.family}_{a.start}_{a.end}_"
        f"{datetime.now(UTC):%Y%m%dT%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"  wrote {out}")
    return 0


def _progress(a, i: int, t0: float, vals: list[float], real_pf: float) -> None:
    if i == 0:
        per = time.time() - t0
        print(f"  first variant took {per:.1f}s -> estimated total "
              f"{per * a.n_perms / 60:.1f} min", flush=True)
    n_asgood = sum(1 for v in vals if v >= real_pf)
    print(f"  {a.null} {i + 1:>4}/{a.n_perms}  PF={vals[-1]:.4f}  "
          f"running frac>=real={(1 + n_asgood) / (2 + i):.4f}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
