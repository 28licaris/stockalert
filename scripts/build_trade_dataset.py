"""
Layer-2 dataset builder: for every base-strategy candidate entry (breakout while in
the dynamic momentum top-N) on the survivorship-clean ohlcv_daily universe, emit
AS-OF features + a triple-barrier win/loss label. No look-ahead in features (all
trailing/shifted); labels use forward bars (that's the outcome, which is allowed).

  poetry run python scripts/build_trade_dataset.py --top 15 --out data/trades.parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.services.sim.ranker import compute_symbol_features  # noqa: E402


def _load() -> pd.DataFrame:
    from app.db.client import get_client
    rows = get_client().query(
        "SELECT symbol, toDate(timestamp) d, open, high, low, close, volume "
        "FROM ohlcv_daily FINAL ORDER BY symbol, d").result_rows
    df = pd.DataFrame(rows, columns=["symbol", "d", "open", "high", "low", "close", "volume"])
    df["d"] = pd.to_datetime(df["d"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


def _wilder_rsi(c: pd.Series, n=14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(h, l, c, n=14) -> pd.Series:
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def _adx(h, l, c, n=14) -> pd.Series:
    up, dn = h.diff(), -l.diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    atr = _atr(h, l, c, n)
    pdi = 100 * plus.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * minus.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


EW_LOOKBACK = 300  # bars fed to the wave engine per as-of labeling (matches the sim source)


def _ew_features(g, i: int, atr_abs: float) -> dict:
    """As-of Elliott Wave features for a candidate at bar i (uses bars ≤ i only —
    the engine's confirmed-pivot filter enforces no look-ahead internally).
    confidence < 0.5 means 'no clear count' per doctrine; we feed the raw value and
    let the model learn the threshold."""
    from app.indicators.pivots import PivotDetector
    from app.signals.elliott.engine import WaveEngine
    out = {"ew_has_count": 0.0, "ew_conf": 0.0, "ew_uncert": 1.0, "ew_motive_up": 0.0,
           "ew_wave3_up": 0.0, "ew_corrective": 0.0, "ew_dist_invalid_atr": 0.0}
    sub = g.iloc[max(0, i - EW_LOOKBACK + 1): i + 1]
    if len(sub) < 40:
        return out
    close, high, low = sub["close"], sub["high"], sub["low"]
    pivots = PivotDetector(period=5, source="hl").detect(close, high, low)
    if len(pivots) < 4:
        return out
    if not hasattr(_ew_features, "_engine"):
        _ew_features._engine = WaveEngine()
    labeling = _ew_features._engine.label(
        pivots, last_price=float(close.iloc[-1]), symbol=str(g["symbol"].iloc[0]),
        interval="1d", as_of_index=len(close) - 1,
        as_of=sub["d"].iloc[-1].to_pydatetime())
    prim = labeling.primary
    if prim is None:
        return out
    cw = prim.current_wave or ""
    out["ew_has_count"] = 1.0
    out["ew_conf"] = float(labeling.confidence)
    out["ew_uncert"] = float(labeling.uncertainty)
    out["ew_motive_up"] = float(prim.direction == "up" and cw in ("1", "3", "5")
                                and prim.structure in ("impulse", "diagonal"))
    out["ew_wave3_up"] = float(prim.direction == "up" and cw == "3")
    out["ew_corrective"] = float(cw in ("A", "B", "C"))
    if atr_abs > 0 and prim.invalidation_price:
        d = (float(close.iloc[-1]) - float(prim.invalidation_price)) / atr_abs
        out["ew_dist_invalid_atr"] = float(np.clip(d, -10.0, 10.0))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--maxhold", type=int, default=60)
    ap.add_argument("--rr", type=float, default=3.0)
    ap.add_argument("--out", default="data/trades.parquet")
    ap.add_argument("--ew", action="store_true",
                    help="add as-of Elliott Wave features (wave state, confidence, "
                         "invalidation distance) to each candidate")
    ap.add_argument("--position-days", action="store_true",
                    help="POSITION semantics: one candidate per tradeable opportunity — "
                         "after a candidate, skip all signal days until that trade's "
                         "triple-barrier resolves (matches what a portfolio can take; "
                         "candidate-day datasets score every day of a streak and bias "
                         "the model toward later/extended entries — EXP-31/35 flip)")
    a = ap.parse_args(argv)

    print("loading ohlcv_daily…", flush=True)
    df = _load()
    print(f"  {len(df):,} rows, {df.symbol.nunique()} symbols", flush=True)

    # SPY for relative strength + regime (as-of)
    spy = df[df.symbol == "SPY"].set_index("d")["close"].sort_index()
    spy_ret60 = spy.pct_change(60)
    spy_sma50 = spy.rolling(50).mean()

    # cross-sectional momentum rank (top-N eligibility), as-of
    wide = df.pivot_table(index="d", columns="symbol", values="close").sort_index()
    mom = wide.pct_change(60)
    elig = mom.rank(axis=1, ascending=False) <= a.top   # True = in top-N that day

    feats = []
    for sym, g in df.groupby("symbol", sort=False):
        g = g.sort_values("d").reset_index(drop=True)
        if len(g) < 220:
            continue
        c, h, l, v = g["close"], g["high"], g["low"], g["volume"]
        prior_hi = h.rolling(20).max().shift(1)
        vol_avg = v.rolling(20).mean().shift(1)
        # SHARED feature code (app.services.sim.ranker) — same fn the live filter
        # calls → guarantees train/inference feature parity by construction.
        g_feat = compute_symbol_features(g)
        g_feat["symbol"] = sym
        g_feat["d"] = g["d"].values
        g_feat["prior_hi"] = prior_hi.values
        # base entry rule = breakout (new 20d high on volume)
        g_feat["is_breakout"] = (c > prior_hi) & (v >= 1.5 * vol_avg)
        # relative strength + regime (align on date)
        g_feat["rel_str"] = g_feat["ret60"].values - g_feat["d"].map(spy_ret60).values
        g_feat["regime_up"] = (g_feat["d"].map(spy) > g_feat["d"].map(spy_sma50)).astype(float)
        # momentum-top-N eligibility
        g_feat["eligible"] = [bool(elig.at[d, sym]) if (d in elig.index and sym in elig.columns) else False
                              for d in g_feat["d"]]
        # forward triple-barrier label on candidate bars
        cand = g_feat[(g_feat["is_breakout"]) & (g_feat["eligible"])].index
        opens, highs, lows, closes = g["open"].values, h.values, l.values, c.values
        n = len(g)
        busy_until = -1  # position-days mode: index until which a trade is open
        for i in cand:
            if a.position_days and i <= busy_until:
                continue  # a real portfolio is still in the prior trade
            if i + 1 >= n:
                continue
            entry = opens[i + 1]                 # fill at next open (no look-ahead)
            stop = g_feat["prior_hi"].values[i]
            risk = entry - stop
            if not (risk > 0) or not np.isfinite(entry):
                continue
            target = entry + a.rr * risk
            label, rmult, held = None, None, None
            for j in range(i + 1, min(i + 1 + a.maxhold, n)):
                if lows[j] <= stop:              # stop checked first (worst case)
                    label, rmult, held = 0, -1.0, j - (i + 1); break
                if highs[j] >= target:
                    label, rmult, held = 1, a.rr, j - (i + 1); break
            if label is None:                    # time exit
                jx = min(i + a.maxhold, n - 1)
                rmult = (closes[jx] - entry) / risk
                label, held = int(rmult > 0), jx - (i + 1)
            if a.position_days:
                busy_until = (i + 1) + held      # occupied through the exit bar
            row = g_feat.loc[i, ["symbol", "d", "ret20", "ret60", "ret120", "rsi", "atr_pct",
                                 "adx", "dist_sma50", "dist_sma200", "vol_ratio", "dollar_vol",
                                 "bo_height", "rel_str", "regime_up"]].to_dict()
            row.update({"label": label, "rmult": float(rmult), "held": int(held), "entry": float(entry)})
            if a.ew:
                atr_abs = float(g_feat["atr_pct"].values[i]) * float(closes[i])
                row.update(_ew_features(g, i, atr_abs))
            feats.append(row)

    out = pd.DataFrame(feats)
    out = out.replace([np.inf, -np.inf], np.nan).dropna()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(a.out, index=False)
    print(f"\n{len(out):,} labeled candidate trades → {a.out}")
    print(f"  base win rate: {out.label.mean()*100:.1f}%  |  avg R: {out.rmult.mean():.3f}")
    print(f"  date span: {out.d.min().date()} → {out.d.max().date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
