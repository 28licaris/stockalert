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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--maxhold", type=int, default=60)
    ap.add_argument("--rr", type=float, default=3.0)
    ap.add_argument("--out", default="data/trades.parquet")
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
        for i in cand:
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
            row = g_feat.loc[i, ["symbol", "d", "ret20", "ret60", "ret120", "rsi", "atr_pct",
                                 "adx", "dist_sma50", "dist_sma200", "vol_ratio", "dollar_vol",
                                 "bo_height", "rel_str", "regime_up"]].to_dict()
            row.update({"label": label, "rmult": float(rmult), "held": int(held), "entry": float(entry)})
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
