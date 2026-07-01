"""
Layer-2 probability ranker — SHARED feature computation + model, used by BOTH the
training dataset builder (scripts/build_trade_dataset.py) and the live MetaRankFilter.

Sharing this one module is what guarantees FEATURE PARITY between training and
inference: the model can only work live if the features fed to it are computed
exactly as they were at training time. Pure (numpy/pandas only) → safe past the
strategies purity gate.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Symbol-only features (market features rel_str/regime_up are appended by callers).
SYMBOL_FEATURES = [
    "ret20", "ret60", "ret120", "rsi", "atr_pct", "adx",
    "dist_sma50", "dist_sma200", "vol_ratio", "dollar_vol", "bo_height",
]
FEATURES = SYMBOL_FEATURES + ["rel_str", "regime_up"]


def wilder_rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    up, dn = h.diff(), -l.diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(h, l, c, n)
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def compute_symbol_features(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized symbol-only features (all trailing/shifted — no look-ahead).
    `df` needs columns open/high/low/close/volume. Returns a frame indexed like df
    with the SYMBOL_FEATURES columns. Used identically for the full-series training
    panel and for the last bar at inference — that's the parity guarantee."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    a = atr(h, l, c)
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    prior_hi = h.rolling(20).max().shift(1)
    vol_avg = v.rolling(20).mean().shift(1)
    return pd.DataFrame({
        "ret20": c.pct_change(20), "ret60": c.pct_change(60), "ret120": c.pct_change(120),
        "rsi": wilder_rsi(c), "atr_pct": a / c, "adx": adx(h, l, c),
        "dist_sma50": (c - sma50) / a, "dist_sma200": (c - sma200) / a,
        "vol_ratio": v / vol_avg,
        "dollar_vol": np.log((c * v).rolling(20).mean().shift(1) + 1),
        "bo_height": (c - prior_hi) / a,
    }, index=df.index)


def load_ranker(path: str = "data/ranker.json") -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    m["_mu"] = np.asarray(m["mu"], dtype=float)
    m["_sd"] = np.asarray(m["sd"], dtype=float)
    m["_w"] = np.asarray(m["w"], dtype=float)
    return m


def predict_proba(model: dict, feats: dict[str, float]) -> float:
    """P(target-before-stop) for one candidate. `feats` must contain every name in
    model['features']; standardized with the training mu/sd."""
    x = np.array([float(feats[f]) for f in model["features"]], dtype=float)
    z = (x - model["_mu"]) / model["_sd"]
    return float(1.0 / (1.0 + math.exp(-(float(z @ model["_w"]) + model["b"]))))
