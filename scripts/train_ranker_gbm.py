"""
Layer-2 ranker, GBM upgrade: train LightGBM on the same trade dataset and the same
TRAIN/HOLDOUT wall as train_ranker.py, and report it HEAD-TO-HEAD vs the logistic
baseline. Adoption rule (honesty doctrine): GBM ships only if it beats logistic on
the untouched holdout. Early stopping validates on the chronological TAIL OF TRAIN —
never the holdout.

  poetry run python scripts/train_ranker_gbm.py --data data/trades.parquet --split 2020-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from train_ranker import EW_FEATURES, _auc, _fit  # noqa: E402
from train_ranker import FEATURES as BASE_FEATURES  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/trades.parquet")
    ap.add_argument("--split", default="2020-01-01")
    ap.add_argument("--val-frac", type=float, default=0.2,
                    help="chronological tail of TRAIN used for early stopping")
    ap.add_argument("--ew", action="store_true",
                    help="include Elliott Wave features (dataset must be built with --ew)")
    a = ap.parse_args(argv)
    FEATURES = BASE_FEATURES + EW_FEATURES if a.ew else BASE_FEATURES
    import lightgbm as lgb

    df = pd.read_parquet(a.data).sort_values("d").reset_index(drop=True)
    tr, ho = df[df.d < a.split], df[df.d >= a.split]
    print(f"TRAIN {len(tr)} trades (<{a.split})  |  HOLDOUT {len(ho)} trades (>={a.split})")

    # -- logistic baseline (identical to train_ranker.py) for a same-data comparison
    mu, sd = tr[FEATURES].mean(), tr[FEATURES].std().replace(0, 1)
    w, b = _fit(((tr[FEATURES] - mu) / sd).values, tr["label"].values.astype(float))
    p_logit = 1 / (1 + np.exp(-(((ho[FEATURES] - mu) / sd).values @ w + b)))

    # -- GBM: early-stop on the chronological tail of TRAIN (no holdout peeking)
    cut = int(len(tr) * (1 - a.val_frac))
    fit, val = tr.iloc[:cut], tr.iloc[cut:]
    model = lgb.train(
        {"objective": "binary", "metric": "auc", "learning_rate": 0.03,
         "num_leaves": 15, "min_data_in_leaf": 40, "feature_fraction": 0.8,
         "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
         "verbosity": -1, "seed": 7},
        lgb.Dataset(fit[FEATURES], label=fit["label"]),
        num_boost_round=2000,
        valid_sets=[lgb.Dataset(val[FEATURES], label=val["label"])],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    p_gbm = model.predict(ho[FEATURES], num_iteration=model.best_iteration)
    yho = ho["label"].values.astype(float)

    print(f"\nbest_iteration={model.best_iteration}")
    print(f"HOLDOUT AUC   logistic {_auc(yho, p_logit):.3f}   |   GBM {_auc(yho, np.asarray(p_gbm)):.3f}")
    print(f"HOLDOUT base: {len(ho)} trades · win {yho.mean()*100:.1f}% · avg R {ho.rmult.mean():+.3f}")

    for name, p in [("logistic", p_logit), ("GBM", np.asarray(p_gbm))]:
        q = np.asarray(pd.qcut(p, 3, labels=["low", "mid", "high"], duplicates="drop"))
        print(f"\n{name} predicted-P tercile → actual outcome (HOLDOUT):")
        for t in ["low", "mid", "high"]:
            m = q == t
            if m.sum():
                print(f"  {t:4}: n={m.sum():4d}  win {yho[m].mean()*100:5.1f}%  "
                      f"avg R {ho.rmult.values[m].mean():+.3f}")
        thr = np.median(p)
        top = p >= thr
        print(f"  gate @ P>=median: win {yho[top].mean()*100:.1f}% (base {yho.mean()*100:.1f}%) · "
              f"avg R {ho.rmult.values[top].mean():+.3f} (base {ho.rmult.mean():+.3f})")

    print("\nGBM feature importance (gain):")
    imp = sorted(zip(FEATURES, model.feature_importance("gain")), key=lambda kv: -kv[1])
    tot = sum(v for _, v in imp) or 1
    for f, v in imp:
        print(f"  {f:12} {v/tot*100:5.1f}%")

    Path("data").mkdir(exist_ok=True)
    model.save_model("data/ranker_gbm.txt", num_iteration=model.best_iteration)
    Path("data/ranker_gbm_meta.json").write_text(json.dumps({
        "features": FEATURES, "split": a.split, "best_iteration": model.best_iteration,
        "train_rows": len(tr), "holdout_rows": len(ho)}, indent=2))
    print("\nsaved model → data/ranker_gbm.txt (+ meta json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
