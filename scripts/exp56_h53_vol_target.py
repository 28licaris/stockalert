"""
EXP-56 H-53: GEX vol-targeting overlay on SPY (registered grid).

Hold SPY continuously; scale exposure by the PRIOR-day GEX vol regime
(trailing-252d percentile bands — the raw zero-cross is banned per the
EXP-55 validation caveat): full exposure in the top band, `bottom_exp`
in the bottom band, full in the middle. Metric: Sharpe of sized daily
returns vs a permutation null (bars permute, GEX series fixed) — tests
whether the real alignment between the GEX vol forecast and realized
returns adds risk-adjusted value. Also reports unsized (buy-and-hold)
Sharpe on the real tape as the effect-size baseline.

  poetry run python scripts/exp56_h53_vol_target.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.services.sim.permutation import permute_frames  # noqa: E402
from scripts.mcpt_insample import _load_frames  # noqa: E402

START, END = "2017-01-01", "2022-12-31"
N_PERMS, SEED = 1000, 86
GRID = [{"bottom_exp": be, "bands": bd}
        for be in (0.0, 0.5) for bd in ((0.40, 0.60), (1 / 3, 2 / 3))]


def _exposure(net_gex: pd.Series, bands: tuple[float, float], bottom_exp: float) -> pd.Series:
    pct = net_gex.rolling(252, min_periods=126).rank(pct=True)
    expo = pd.Series(1.0, index=net_gex.index)
    expo[pct < bands[0]] = bottom_exp
    return expo.shift(1).fillna(1.0)  # knowability: use yesterday's regime


def _sharpe(rets: pd.Series) -> float:
    sd = rets.std()
    return float(rets.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def main() -> int:
    frames = _load_frames(["SPY"], START, END)
    spy = frames["SPY"]
    feats = pd.read_parquet("data/mcpt/gex_features.parquet")
    g = feats[feats.symbol == "SPY"].set_index("date").sort_index()["net_gex"]
    g = g[~g.index.duplicated(keep="last")]

    def eval_all(frame: pd.DataFrame) -> dict[int, float]:
        r = np.log(frame["close"]).diff()
        rd = pd.Series(r.values, index=frame.index.date)
        net = g.reindex(rd.index)
        out = {}
        for i, cfg in enumerate(GRID):
            expo = _exposure(net, cfg["bands"], cfg["bottom_exp"])
            out[i] = _sharpe((rd * expo).dropna())
        return out

    real = eval_all(spy)
    best_i = max(real, key=real.get)
    r_all = np.log(spy["close"]).diff().dropna()
    print(f"buy-and-hold Sharpe: {_sharpe(r_all):.3f}")
    for i, cfg in enumerate(GRID):
        print(f"cfg {cfg}: real sized Sharpe = {real[i]:.3f}"
              + ("   <- best" if i == best_i else ""), flush=True)

    ge = 0
    null_best = []
    for k in range(1, N_PERMS + 1):
        perm = permute_frames({"SPY": spy}, seed=SEED + k)["SPY"]
        pbest = max(eval_all(perm).values())
        null_best.append(pbest)
        if pbest >= real[best_i]:
            ge += 1
        if k % 100 == 0:
            print(f"  perm {k}/{N_PERMS} running p={(ge + 1) / (k + 1):.4f}", flush=True)

    p = (ge + 1) / (N_PERMS + 1)
    nb = np.asarray(null_best)
    print(f"\nH-53 FINAL: best real Sharpe {real[best_i]:.3f} (cfg {GRID[best_i]}) vs "
          f"null best-of-grid {nb.mean():.3f}±{nb.std():.3f} -> p = {p:.4f}")
    Path("data/mcpt/exp56_h53_vol_target.json").write_text(json.dumps({
        "real": {str(GRID[i]): real[i] for i in real}, "best": GRID[best_i],
        "p": p, "null_mean": float(nb.mean()), "null_sd": float(nb.std()),
        "n_perms": N_PERMS, "seed": SEED, "window": [START, END],
    }, indent=2))
    print("wrote data/mcpt/exp56_h53_vol_target.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
