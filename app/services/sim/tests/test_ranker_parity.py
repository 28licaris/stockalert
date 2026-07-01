"""Ranker feature parity + no-look-ahead, and the MetaRank gate logic (no CH)."""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.services.sim.context import Context
from app.services.sim.filters import MetaRankFilter, build_filter
from app.services.sim.ranker import FEATURES, SYMBOL_FEATURES, compute_symbol_features, predict_proba
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot
from app.services.sim.signal_source import Signal

UTC = dt.timezone.utc
T0 = dt.datetime(2020, 1, 1, tzinfo=UTC)


def _series(n=260):
    idx = pd.date_range(T0, periods=n, freq="D")
    c = 100 + np.cumsum(np.sin(np.arange(n) / 6.0) + 0.05)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c,
                         "volume": 1_000_000.0}, index=idx)


def test_feature_computation_is_causal_and_parity():
    # Feature at bar i on the FULL series must equal the last-bar feature on the
    # series truncated at i → the live filter (ctx.history up to now) gets the SAME
    # value the training panel (full series) stored. Parity + no look-ahead.
    df = _series(260)
    full = compute_symbol_features(df)
    for i in (210, 230, 259):
        trunc = compute_symbol_features(df.iloc[: i + 1]).iloc[-1]
        for f in SYMBOL_FEATURES:
            a, b = full.iloc[i][f], trunc[f]
            if pd.notna(a) or pd.notna(b):
                assert abs(float(a) - float(b)) < 1e-9, f"{f} mismatch at bar {i}"


def test_predict_proba_matches_manual():
    model = {"features": FEATURES, "mu": [0.0] * len(FEATURES), "sd": [1.0] * len(FEATURES),
             "w": [0.0] * len(FEATURES), "b": 0.0}
    model["_mu"] = np.zeros(len(FEATURES)); model["_sd"] = np.ones(len(FEATURES)); model["_w"] = np.zeros(len(FEATURES))
    feats = {f: 0.5 for f in FEATURES}
    assert abs(predict_proba(model, feats) - 0.5) < 1e-9   # all-zero weights → 0.5


@dataclass
class _Bar:
    symbol: str; timestamp: dt.datetime; open: float; high: float; low: float; close: float; volume: float


def _ctx(n=260):
    ctx = Context(config=BacktestConfig(symbols=["X"], start=T0, end=T0 + dt.timedelta(days=400),
                                        interval="1d", history_window=400))
    c = 100.0
    for i in range(n):
        c += math.sin(i / 6.0) + 0.05
        ctx.advance(_Bar("X", T0 + dt.timedelta(days=i), c, c + 1, c - 1, c, 1_000_000.0),
                    PortfolioSnapshot(cash=40_000.0, equity=40_000.0, positions={}, n_trades=0))

    class _Mkt:
        benchmark = "SPY"
        def return_over_asof(self, ts, n): return 0.05
        def above_ma_asof(self, ts, n): return True
    ctx.market = _Mkt()
    return ctx


def test_meta_rank_gate_threshold(tmp_path):
    # tiny model: weight only on bo_height; high threshold vs low → gate flips.
    model = {"features": FEATURES, "mu": [0.0] * len(FEATURES), "sd": [1.0] * len(FEATURES),
             "w": [0.0] * len(FEATURES), "b": 2.0}   # b=2 → P≈0.88 baseline
    (tmp_path / "m.json").write_text(json.dumps(model))
    sig = Signal("X", "long", entry=100, stop=95, target_1=115, kind="stub")
    ctx = _ctx()
    hi = MetaRankFilter(model_path=str(tmp_path / "m.json"), min_proba=0.5).evaluate(ctx, sig)
    lo = MetaRankFilter(model_path=str(tmp_path / "m.json"), min_proba=0.95).evaluate(ctx, sig)
    assert hi.passed and hi.score > 0.5        # P≈0.88 ≥ 0.5 → pass, confidence=P
    assert not lo.passed                        # 0.88 < 0.95 → gated out


def test_registered():
    assert build_filter("meta_rank") is not None
