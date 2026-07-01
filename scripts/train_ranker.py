"""
Layer-2 probability ranker: train a logistic model (numpy — no new deps) on TRAIN
trades to predict P(target-before-stop) from AS-OF features, then prove on the
untouched HOLDOUT that gating by predicted-P raises win-rate / avg-R. Standardization
uses TRAIN stats only (no leakage). Saves the model to data/ranker.json.

  poetry run python scripts/train_ranker.py --data data/trades.parquet --split 2020-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

FEATURES = ["ret20", "ret60", "ret120", "rsi", "atr_pct", "adx", "dist_sma50",
            "dist_sma200", "vol_ratio", "dollar_vol", "bo_height", "rel_str", "regime_up"]
EW_FEATURES = ["ew_has_count", "ew_conf", "ew_uncert", "ew_motive_up", "ew_wave3_up",
               "ew_corrective", "ew_dist_invalid_atr"]


def _auc(y, p) -> float:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty_like(order, dtype=float); ranks[order] = np.arange(1, len(order) + 1)
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def _fit(X, y, epochs=4000, lr=0.2, l2=1e-3):
    w, b, n = np.zeros(X.shape[1]), 0.0, len(y)
    for _ in range(epochs):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - y
        w -= lr * (X.T @ g / n + l2 * w)
        b -= lr * g.mean()
    return w, b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/trades.parquet")
    ap.add_argument("--split", default="2020-01-01")
    ap.add_argument("--ew", action="store_true",
                    help="include Elliott Wave features (dataset must be built with --ew)")
    a = ap.parse_args(argv)
    global FEATURES
    if a.ew:
        FEATURES = FEATURES + EW_FEATURES
    df = pd.read_parquet(a.data).sort_values("d").reset_index(drop=True)
    tr, ho = df[df.d < a.split], df[df.d >= a.split]
    print(f"TRAIN {len(tr)} trades (<{a.split})  |  HOLDOUT {len(ho)} trades (>={a.split})")

    mu, sd = tr[FEATURES].mean(), tr[FEATURES].std().replace(0, 1)
    Xtr = ((tr[FEATURES] - mu) / sd).values
    Xho = ((ho[FEATURES] - mu) / sd).values
    ytr, yho = tr["label"].values.astype(float), ho["label"].values.astype(float)
    w, b = _fit(Xtr, ytr)
    pho = 1 / (1 + np.exp(-(Xho @ w + b)))

    print(f"\nHOLDOUT AUC = {_auc(yho, pho):.3f}  (0.5 = no signal)")
    print(f"HOLDOUT base: {len(ho)} trades · win {yho.mean()*100:.1f}% · avg R {ho.rmult.mean():+.3f}")
    print("\npredicted-P tercile → actual outcome (HOLDOUT, OOS):")
    q = np.asarray(pd.qcut(pho, 3, labels=["low", "mid", "high"], duplicates="drop"))
    for name in ["low", "mid", "high"]:
        m = q == name
        if m.sum():
            print(f"  {name:4}: n={m.sum():4d}  win {yho[m].mean()*100:5.1f}%  avg R {ho.rmult.values[m].mean():+.3f}")
    # take-top-half vs take-all
    thr = np.median(pho)
    top = pho >= thr
    print(f"\ngate @ predicted-P>=median: take {top.sum()}/{len(ho)} trades · "
          f"win {yho[top].mean()*100:.1f}% (base {yho.mean()*100:.1f}%) · "
          f"avg R {ho.rmult.values[top].mean():+.3f} (base {ho.rmult.mean():+.3f})")
    print("\nfeature weights (standardized; + → more likely a winner):")
    for f, wi in sorted(zip(FEATURES, w), key=lambda kv: -abs(kv[1])):
        print(f"  {f:12} {wi:+.3f}")

    # Train-set predicted-P distribution: the a-priori gate threshold (median) and
    # the sizing-calibration quantiles (p10→conf 0, p90→conf 1). TRAIN-only stats.
    ptr = 1 / (1 + np.exp(-(Xtr @ w + b)))
    q10, q50, q90 = (float(np.quantile(ptr, q)) for q in (0.10, 0.50, 0.90))
    print(f"\nTRAIN predicted-P quantiles: p10={q10:.3f}  median={q50:.3f}  p90={q90:.3f}"
          f"  (median = the a-priori min_proba; p10/p90 = sizing calibration)")

    Path("data/ranker.json").write_text(json.dumps({
        "features": FEATURES, "mu": mu.tolist(), "sd": sd.tolist(),
        "w": w.tolist(), "b": float(b),
        "train_p10": q10, "train_p50": q50, "train_p90": q90}, indent=2))
    print("saved model → data/ranker.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
