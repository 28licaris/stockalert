"""
DT-3 — the conditioning experiment (the last pre-registered arrow for the
mechanical setup families).

Per family: attach every PRE-ENTRY feature we have (pre-open scan context,
trigger time, risk geometry, D-1 market regime), train P(win) on DEV
2006-2018 (logistic + LightGBM, early stop on the chronological tail of DEV),
then sort the untouched HOLDOUT (2019-2026 ex-2024) into predicted-quality
deciles and report realized avg R per decile.

PRE-REGISTERED VERDICT RULE: a family survives iff its TOP holdout decile
earns avg R ≥ +0.15 at 1× slippage (clears costs with margin). Otherwise the
family is dead — no context rescues it.

  poetry run python scripts/daytrade_condition.py --version v2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

DEV_MAX_YEAR = 2018
FEATURES = ["gap_pct", "abs_gap", "log_pm_dv", "pm_range_pct", "pm_vs_prev_vol",
            "prev_range_pct", "prev_close_pos", "log_price", "scan_rank",
            "entry_min", "risk_pct", "side_long", "spy_above_ma50", "spy_ret1"]


def _load(version: str) -> pd.DataFrame:
    from app.db.client import get_client
    cli = get_client()
    trades = pd.read_parquet(f"data/dt_trades_all_{version}.parquet")
    trades = trades[trades.slip == 1.0].copy()

    scan = pd.DataFrame(cli.query(
        "SELECT day, symbol, rank, prev_close, prev_high, prev_low, prev_volume, "
        "prev_dollar_vol, pm_last, pm_high, pm_low, pm_volume, pm_dollar_vol "
        "FROM daytrade_scan FINAL").result_rows,
        columns=["day", "symbol", "rank", "prev_close", "prev_high", "prev_low",
                 "prev_volume", "prev_dollar_vol", "pm_last", "pm_high", "pm_low",
                 "pm_volume", "pm_dollar_vol"])
    spy = pd.DataFrame(cli.query(
        "SELECT toDate(timestamp) d, close FROM ohlcv_daily FINAL "
        "WHERE symbol='SPY' ORDER BY d").result_rows, columns=["d", "spy_close"])
    spy["spy_ma50"] = spy.spy_close.rolling(50).mean()
    spy["spy_above_ma50"] = (spy.spy_close > spy.spy_ma50).astype(float)
    spy["spy_ret1"] = spy.spy_close.pct_change()
    # as-of D-1: shift so day D sees yesterday's regime (no same-day leakage)
    spy[["spy_above_ma50", "spy_ret1"]] = spy[["spy_above_ma50", "spy_ret1"]].shift(1)

    df = trades.merge(scan, on=["day", "symbol"], how="left")
    df = df.merge(spy[["d", "spy_above_ma50", "spy_ret1"]],
                  left_on="day", right_on="d", how="left")

    rng = (df.prev_high - df.prev_low).replace(0, np.nan)
    df["abs_gap"] = df.gap_pct.abs()
    df["log_pm_dv"] = np.log10(df.pm_dollar_vol.clip(lower=1))
    df["pm_range_pct"] = (df.pm_high - df.pm_low) / df.prev_close
    df["pm_vs_prev_vol"] = (df.pm_volume / df.prev_volume.replace(0, np.nan)).clip(upper=10)
    df["prev_range_pct"] = rng / df.prev_close
    df["prev_close_pos"] = ((df.prev_close - df.prev_low) / rng).clip(0, 1)
    df["log_price"] = np.log10(df.prev_close.clip(lower=0.1))
    df["scan_rank"] = df["rank"].astype(float)
    df["risk_pct"] = np.nan  # placeholder — not in the dump; keep column for parity
    df["side_long"] = (df.side == "long").astype(float)
    df["label"] = (df.r > 0).astype(float)
    df = df.drop(columns=["risk_pct"])
    return df.replace([np.inf, -np.inf], np.nan).dropna(
        subset=[f for f in FEATURES if f != "risk_pct"])


def _decile_table(p: np.ndarray, r: np.ndarray) -> str:
    q = pd.qcut(p, 10, labels=False, duplicates="drop")
    lines = [f"  {'decile':>7}{'n':>8}{'win%':>8}{'avgR':>8}"]
    for d in sorted(np.unique(q)):
        m = q == d
        lines.append(f"  {int(d) + 1:>7}{m.sum():>8}{(r[m] > 0).mean() * 100:>8.1f}"
                     f"{r[m].mean():>8.3f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=("v1", "v2"), default="v2")
    a = ap.parse_args(argv)
    import lightgbm as lgb

    feats = [f for f in FEATURES if f != "risk_pct"]
    df = _load(a.version)
    print(f"{len(df):,} trades with features ({a.version}, 1× slip)")

    verdicts = []
    for setup, g in df.groupby("setup"):
        dev = g[g.year <= DEV_MAX_YEAR].sort_values("day")
        hold = g[g.year > DEV_MAX_YEAR]
        if len(dev) < 3000 or len(hold) < 1500:
            print(f"\n### {setup}: insufficient sample (dev {len(dev)}, hold {len(hold)})")
            continue
        print(f"\n### {setup} — dev {len(dev):,} (win {dev.label.mean()*100:.1f}%) · "
              f"hold {len(hold):,} (win {hold.label.mean()*100:.1f}%, "
              f"base avgR {hold.r.mean():+.3f})")

        mu, sd = dev[feats].mean(), dev[feats].std().replace(0, 1)
        Xd = ((dev[feats] - mu) / sd).values
        Xh = ((hold[feats] - mu) / sd).values
        yd = dev.label.values

        w = np.zeros(Xd.shape[1]); b = 0.0
        for _ in range(3000):
            pr = 1 / (1 + np.exp(-(Xd @ w + b)))
            gvec = pr - yd
            w -= 0.2 * (Xd.T @ gvec / len(yd) + 1e-3 * w)
            b -= 0.2 * gvec.mean()
        p_log = 1 / (1 + np.exp(-(Xh @ w + b)))

        cut = int(len(dev) * 0.8)
        model = lgb.train(
            {"objective": "binary", "metric": "auc", "learning_rate": 0.05,
             "num_leaves": 31, "min_data_in_leaf": 200, "feature_fraction": 0.8,
             "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
             "verbosity": -1, "seed": 7},
            lgb.Dataset(dev[feats].iloc[:cut], label=yd[:cut]),
            num_boost_round=1500,
            valid_sets=[lgb.Dataset(dev[feats].iloc[cut:], label=yd[cut:])],
            callbacks=[lgb.early_stopping(75, verbose=False)])
        p_gbm = np.asarray(model.predict(hold[feats], num_iteration=model.best_iteration))

        r = hold.r.values
        for name, p in (("logistic", p_log), ("GBM", p_gbm)):
            q = pd.qcut(p, 10, labels=False, duplicates="drop")
            top = q == q.max()
            print(f"  {name}: holdout top-decile avgR {r[top].mean():+.3f} "
                  f"(n={top.sum()}, win {(r[top] > 0).mean()*100:.1f}%)")
        print(_decile_table(p_gbm, r))
        imp = sorted(zip(feats, model.feature_importance("gain")), key=lambda kv: -kv[1])[:5]
        print("  top features: " + ", ".join(f"{f}({v/sum(x for _, x in imp)*100:.0f}%)"
                                             for f, v in imp))
        best_top = max(r[pd.qcut(p, 10, labels=False, duplicates='drop') ==
                         pd.qcut(p, 10, labels=False, duplicates='drop').max()].mean()
                       for p in (p_log, p_gbm))
        verdicts.append((setup, best_top, "SURVIVES" if best_top >= 0.15 else "DEAD"))

    print("\n=== PRE-REGISTERED VERDICTS (top holdout decile ≥ +0.15R @1× slip) ===")
    for setup, top, v in verdicts:
        print(f"  {setup:20} top-decile avgR {top:+.3f} → {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
