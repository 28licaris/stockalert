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

from app.services.sim.permutation import permute_frames  # noqa: E402
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
}


def _pooled_pf(sig: pd.DataFrame, fwd_ret: pd.DataFrame) -> float:
    rets = (sig * fwd_ret).to_numpy().ravel()
    rets = rets[np.isfinite(rets)]
    gains = rets[rets > 0].sum()
    losses = abs(rets[rets < 0].sum())
    return float(gains / losses) if losses > 0 else 0.0


def _optimize(family: str, frames: dict[str, pd.DataFrame]) -> tuple[dict, float]:
    wide = {
        col: pd.DataFrame({s: f[col] for s, f in frames.items()}).sort_index()
        for col in ("close", "volume")
    }
    fwd_ret = np.log(wide["close"]).diff().shift(-1)
    best_params, best_pf = {}, 0.0
    for params, fn in FAMILIES[family]:
        pf = _pooled_pf(fn(wide, **params), fwd_ret)
        if pf > best_pf:
            best_params, best_pf = params, pf
    return best_params, best_pf


# ── data ─────────────────────────────────────────────────────────────


def _load_frames(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    from app.db.client import get_client

    rows = get_client().query(
        "SELECT symbol, toDate(timestamp) d, open, high, low, close, volume "
        "FROM ohlcv_daily FINAL "
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
    ap.add_argument("--out", default=None, help="JSON result path (default data/mcpt/)")
    a = ap.parse_args(argv)

    if a.symbols:
        symbols = a.symbols
    elif a.config:
        symbols = yaml.safe_load(Path(a.config).read_text())["symbols"]
    else:
        raise SystemExit("supply --symbols or --config")

    frames = _load_frames(symbols, a.start, a.end)
    real_params, real_pf = _optimize(a.family, frames)
    print(f"REAL  {a.family}: best={real_params} pooled_PF={real_pf:.4f}", flush=True)

    permuted_pfs: list[float] = []
    t0 = time.time()
    for i in range(a.n_perms):
        perm = permute_frames(frames, seed=a.seed + i)
        p_params, p_pf = _optimize(a.family, perm)
        permuted_pfs.append(p_pf)
        if i == 0:
            per = time.time() - t0
            print(f"  first permutation took {per:.1f}s -> estimated total "
                  f"{per * a.n_perms / 60:.1f} min", flush=True)
        n_asgood = sum(1 for v in permuted_pfs if v >= real_pf)
        print(f"  perm {i + 1:>4}/{a.n_perms}  PF={p_pf:.4f}  best={p_params}  "
              f"running p={(1 + n_asgood) / (2 + i):.4f}", flush=True)

    res = mcpt_pvalue(real_pf, permuted_pfs, greater_is_better=True)
    print(f"\nIN-SAMPLE MCPT [{a.family}] {a.start}..{a.end} "
          f"({len(frames)} symbols, {a.n_perms} permutations)")
    print(f"  {res.summary()}")

    out = Path(a.out) if a.out else Path("data/mcpt") / (
        f"insample_{a.family}_{a.start}_{a.end}_{datetime.now(UTC):%Y%m%dT%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "kind": "insample_mcpt", "family": a.family, "start": a.start, "end": a.end,
        "n_symbols": len(frames), "seed": a.seed, "real_params": real_params,
        "result": res.model_dump(), "permuted_pfs": permuted_pfs,
    }, indent=2))
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
