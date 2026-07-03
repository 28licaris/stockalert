"""
Drawdown confidence bands via equity-curve resampling (Build Alpha
"reshuffle"/"resample" Monte Carlo).

One backtest shows ONE drawdown path. This runs the portfolio once,
takes its daily equity log-returns, and generates N alternate paths two
ways:

  reshuffle — permute the return order (same P&L, alternate orderings)
  resample  — draw returns with replacement (wider outcome distribution)

and reports max-drawdown percentiles for each. Use it to state risk
properties honestly: "observed DD −15%, 95th percentile of resampled
paths −X%" instead of quoting the single observed path. `--block`
resamples in contiguous blocks to respect volatility clustering.

Usage:
  poetry run python scripts/dd_resample.py --config configs/dyn_breakout_v2_top50_brake.yaml \
      --start 2006-01-01T00:00:00Z --end 2021-12-31T23:59:59Z --n 10000 --block 5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from app.services.sim.backtester import Backtester  # noqa: E402
from scripts.mcpt_walkforward import _build_cfg  # noqa: E402
from scripts.run_backtest import _load_strategy  # noqa: E402

UTC = timezone.utc
PCTS = (50, 90, 95, 99)


def _max_dd(log_rets: np.ndarray) -> float:
    equity = np.exp(np.cumsum(log_rets))
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1.0).min())


def _blocks(rets: np.ndarray, block: int, rng, replace: bool) -> np.ndarray:
    if block <= 1:
        return rng.choice(rets, size=len(rets), replace=replace) if replace \
            else rng.permutation(rets)
    n_blocks = int(np.ceil(len(rets) / block))
    if replace:
        starts = rng.integers(0, max(len(rets) - block, 1), size=n_blocks)
    else:
        starts = rng.permutation(np.arange(0, len(rets) - block + 1, block))
    out = np.concatenate([rets[s:s + block] for s in starts])
    return out[: len(rets)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--n", type=int, default=10_000)
    ap.add_argument("--block", type=int, default=1,
                    help="block length (>1 respects volatility clustering)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    r = yaml.safe_load(Path(a.config).read_text())
    cfg = _build_cfg(r, a.start, a.end)
    bt = Backtester()
    bt._capture_snapshot = lambda *args, **kw: None  # type: ignore[assignment]
    print(f"running {Path(a.config).stem} {str(cfg.start)[:10]}..{str(cfg.end)[:10]} once…",
          flush=True)
    strat = _load_strategy(r["strategy"], r.get("strategy_params", {}), interval=cfg.interval)
    res = bt.run_portfolio(strat, cfg)
    equity = np.asarray([e for _, e in res.equity_curve], dtype=float)
    if len(equity) < 30:
        raise SystemExit(f"equity curve too short ({len(equity)} points) — wrong window?")
    rets = np.diff(np.log(equity))
    observed_dd = _max_dd(rets)
    print(f"  observed: max DD {observed_dd:+.1%}  ({len(rets)} daily returns, "
          f"total return {res.metrics.total_return:+.1%})", flush=True)

    rng = np.random.default_rng(a.seed)
    out_stats = {}
    for mode, replace in (("reshuffle", False), ("resample", True)):
        dds = np.array([_max_dd(_blocks(rets, a.block, rng, replace))
                        for _ in range(a.n)])
        stats = {f"p{p}": float(np.percentile(dds, 100 - p)) for p in PCTS}
        worst = float(dds.min())
        out_stats[mode] = {**stats, "worst": worst}
        print(f"  {mode:>9} (n={a.n}, block={a.block}): "
              + "  ".join(f"p{p}={stats[f'p{p}']:+.1%}" for p in PCTS)
              + f"  worst={worst:+.1%}", flush=True)

    out = Path(a.out) if a.out else Path("data/mcpt") / (
        f"dd_resample_{Path(a.config).stem}_{str(cfg.start)[:10]}_{str(cfg.end)[:10]}_"
        f"{datetime.now(UTC):%Y%m%dT%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "kind": "dd_resample", "config": str(a.config),
        "start": str(cfg.start), "end": str(cfg.end), "n": a.n, "block": a.block,
        "seed": a.seed, "observed_dd": observed_dd,
        "observed_total_return": res.metrics.total_return, **out_stats,
    }, indent=2))
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
