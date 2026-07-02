"""
Label-permutation null for the Layer-2 probability ranker.

Question: is the ranker's holdout AUC distinguishable from a model
trained on pure noise? Shuffle the TRAIN labels N times (features,
split, standardization, and the REAL holdout labels untouched), retrain
the identical logistic model each time, and read the null distribution
of holdout AUC. p = P(null AUC >= real AUC).

This complements the bar-permutation MCPT (which nulls the *price*
structure); label permutation nulls the *feature→outcome* link while
keeping every marginal distribution intact.

Usage:
  poetry run python scripts/mcpt_ranker_labels.py --data data/trades_pos50.parquet \
      --split 2020-01-01 --n-perms 500
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

from app.services.sim.significance import mcpt_pvalue  # noqa: E402
from scripts.train_ranker import FEATURES, _auc, _fit  # noqa: E402

UTC = timezone.utc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/trades_pos50.parquet")
    ap.add_argument("--split", default="2020-01-01")
    ap.add_argument("--n-perms", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None, help="JSON result path (default data/mcpt/)")
    a = ap.parse_args(argv)

    df = pd.read_parquet(a.data).sort_values("d").reset_index(drop=True)
    tr, ho = df[df.d < a.split], df[df.d >= a.split]
    if tr.empty or ho.empty:
        raise SystemExit(f"empty split: TRAIN {len(tr)} / HOLDOUT {len(ho)} at {a.split}")
    print(f"TRAIN {len(tr)} trades (<{a.split})  |  HOLDOUT {len(ho)} trades (>={a.split})",
          flush=True)

    mu, sd = tr[FEATURES].mean(), tr[FEATURES].std().replace(0, 1)
    x_tr = ((tr[FEATURES] - mu) / sd).values
    x_ho = ((ho[FEATURES] - mu) / sd).values
    y_tr = tr["label"].values.astype(float)
    y_ho = ho["label"].values.astype(float)

    w, b = _fit(x_tr, y_tr)
    p_ho = 1 / (1 + np.exp(-(x_ho @ w + b)))
    real_auc = _auc(y_ho, p_ho)
    tercile = np.quantile(p_ho, 2 / 3)
    top = p_ho >= tercile
    print(f"REAL holdout AUC = {real_auc:.4f}  "
          f"top-tercile: win {y_ho[top].mean() * 100:.1f}% avg R "
          f"{ho.rmult.values[top].mean():+.3f} (base {y_ho.mean() * 100:.1f}% / "
          f"{ho.rmult.mean():+.3f})", flush=True)

    rng = np.random.default_rng(a.seed)
    null_aucs: list[float] = []
    t0 = time.time()
    for i in range(a.n_perms):
        y_shuf = rng.permutation(y_tr)
        w_i, b_i = _fit(x_tr, y_shuf)
        auc_i = _auc(y_ho, 1 / (1 + np.exp(-(x_ho @ w_i + b_i))))
        null_aucs.append(float(auc_i))
        if i == 0:
            per = time.time() - t0
            print(f"  first fit took {per:.2f}s -> estimated total "
                  f"{per * a.n_perms / 60:.1f} min", flush=True)
        n_asgood = sum(1 for v in null_aucs if v >= real_auc)
        print(f"  perm {i + 1:>4}/{a.n_perms}  AUC={auc_i:.4f}  "
              f"running p={(1 + n_asgood) / (2 + i):.4f}", flush=True)

    res = mcpt_pvalue(real_auc, null_aucs, greater_is_better=True)
    print(f"\nRANKER LABEL-PERMUTATION NULL ({a.data}, split {a.split}, "
          f"{a.n_perms} permutations)")
    print(f"  {res.summary()}")

    out = Path(a.out) if a.out else Path("data/mcpt") / (
        f"ranker_labels_{Path(a.data).stem}_{datetime.now(UTC):%Y%m%dT%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "kind": "ranker_label_permutation", "data": str(a.data), "split": a.split,
        "seed": a.seed, "result": res.model_dump(), "null_aucs": null_aucs,
    }, indent=2))
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
