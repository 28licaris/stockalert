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


def _state(close: pd.DataFrame, enter: pd.DataFrame, exit_: pd.DataFrame) -> pd.DataFrame:
    """Long/flat state machine: 1 on `enter`, 0 on `exit_`, held in between."""
    sig = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    return sig.mask(enter, 1.0).mask(exit_, 0.0).ffill().fillna(0.0)


def _wilder_rsi_wide(close: pd.DataFrame, n: int) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _meanrev_rsi(wide, n: int, entry: float, exit: float) -> pd.DataFrame:
    rsi = _wilder_rsi_wide(wide["close"], n)
    return _state(wide["close"], rsi < entry, rsi > exit)


def _meanrev_zscore(wide, n: int, z: float) -> pd.DataFrame:
    close = wide["close"]
    sma = close.rolling(n).mean()
    zs = (close - sma) / close.rolling(n).std()
    return _state(close, zs < -z, zs >= 0)


def _xsec_momentum(wide, lookback, bucket: float, rebalance: int = 21) -> pd.DataFrame:
    """Long the top cross-sectional bucket by trailing return, 21-bar rebalance."""
    close = wide["close"]
    if lookback == "12-1":
        mom = close.shift(21) / close.shift(252) - 1.0
    else:
        mom = close / close.shift(int(lookback)) - 1.0
    rb = mom.iloc[::rebalance]
    thresh = rb.quantile(bucket, axis=1)
    member = rb.ge(thresh, axis=0) & rb.notna()
    return member.reindex(close.index).ffill().fillna(False).astype(float)


def _vol_compression(wide, atr_pctile: float, breakout: int) -> pd.DataFrame:
    """ATR-percentile squeeze arms the symbol; range break enters, range low exits."""
    close, high, low = wide["close"], wide["high"], wide["low"]
    prev_c = close.shift(1)
    tr = np.maximum.reduce([
        (high - low).to_numpy(), (high - prev_c).abs().to_numpy(),
        (low - prev_c).abs().to_numpy()])
    tr = pd.DataFrame(tr, index=close.index, columns=close.columns)
    atr_pct = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / close
    armed = atr_pct.rolling(252).rank(pct=True) <= atr_pctile
    hh = close.rolling(breakout).max().shift(1)
    ll = close.rolling(breakout).min().shift(1)
    return _state(close, armed & (close > hh), close < ll)


def _gap(wide, direction: str, gap: float, hold: int) -> pd.DataFrame:
    """Overnight gap trigger (enter at trigger close), fixed `hold`-bar exposure."""
    open_, close = wide["open"], wide["close"]
    g = open_ / close.shift(1) - 1.0
    trigger = ((g > gap) if direction == "follow" else (g < -gap)).astype(float)
    return trigger.rolling(hold, min_periods=1).max().fillna(0.0)


def _high_52wk(wide, prox: float, pullback: int) -> pd.DataFrame:
    """Pullback low while within `prox` of the 52-wk high; exit at 2x prox distance."""
    close = wide["close"]
    hi52 = close.rolling(252).max()
    near = close >= hi52 * (1 - prox)
    at_pullback_low = close <= close.rolling(pullback).min()
    return _state(close, near & at_pullback_low, close < hi52 * (1 - 2 * prox))


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
    # EXP-39 battery — registered in docs/research_hypotheses.md H-3..H-8
    # BEFORE implementation; grids must match the registry exactly.
    "meanrev_rsi": [
        ({"n": n, "entry": en, "exit": ex}, _meanrev_rsi)
        for n in (2, 3, 4) for en in (10, 15, 20, 25, 30) for ex in (50, 70)
    ],
    "meanrev_zscore": [
        ({"n": n, "z": z}, _meanrev_zscore) for n in (10, 20) for z in (1.5, 2.0, 2.5)
    ],
    "xsec_momentum": [
        ({"lookback": lb, "bucket": b}, _xsec_momentum)
        for lb in (60, 120, "12-1") for b in (0.9, 0.8)
    ],
    "vol_compression": [
        ({"atr_pctile": p, "breakout": bo}, _vol_compression)
        for p in (0.10, 0.20) for bo in (5, 10)
    ],
    "gap": [
        ({"direction": d, "gap": g, "hold": h}, _gap)
        for d in ("follow", "fade") for g in (0.01, 0.02, 0.03) for h in (1, 3, 5)
    ],
    "high_52wk": [
        ({"prox": p, "pullback": pb}, _high_52wk) for p in (0.02, 0.05, 0.10) for pb in (3, 5)
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
        for col in ("open", "high", "low", "close", "volume")
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
