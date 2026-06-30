"""
Out-of-sample search — the honesty harness.

Search a parameter/filter GRID on a DEVELOPMENT window (across the basket),
pick the single best config by an objective, then report that config's
performance on an UNTOUCHED HOLDOUT window. Only the holdout number is a
trustworthy read; the dev number is what we optimized and will be inflated.

This is what lets us *trust* any signal/filter we add — including future
professional TA indicators/strategies. The grid combines source params with
candidate filter stacks (confluence), so "high-confidence A+" = the combo that
holds up out-of-sample, not the one that looked best in-sample.

Usage:
    poetry run python scripts/oos_search.py --config configs/oos_breakout.yaml

Config (YAML):
    strategy: alert_driven
    base:        { source: breakout, risk_pct: 0.01, min_reward_risk: 0.0 }
    grid:
      source_params:                 # cartesian product of these lists
        lookback: [10, 20, 55]
        reward_risk_mult: [1.5, 2.0, 3.0]
      filter_sets:                   # candidate confluence stacks (pick one)
        - []
        - [{ name: trend,  params: { period: 50 } }]
        - [{ name: regime, params: { ma_period: 50 } }]
    symbols: [AAPL, MSFT, ...]
    benchmark: SPY
    interval: 1d
    starting_cash: 40000
    history_window: 250
    dev:     { start: 2022-01-01T00:00:00Z, end: 2023-12-31T23:59:59Z }
    holdout: { start: 2024-01-01T00:00:00Z, end: 2025-12-31T23:59:59Z }
    objective: median_return         # median_return|mean_return|pct_profitable|mean_win_rate
    min_trades: 30                   # ignore degenerate combos below this basket trade count
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from scripts.strategy_sweep import _aggregate, _pct, _run_one  # noqa: E402


def _basket_agg(
    strategy: str, params: dict[str, Any], symbols: list[str],
    window: dict[str, str], interval: str, cash: float, hw: int, benchmark: Optional[str],
) -> dict[str, Any]:
    rows = []
    for sym in symbols:
        r = _run_one(strategy, params, sym, window["start"], window["end"],
                     interval, cash, hw, benchmark)
        if r is not None:
            rows.append(r)
    return _aggregate(rows)


def _grid_combos(grid: dict[str, Any]) -> list[dict[str, Any]]:
    """Cartesian product of source_params lists × filter_sets options."""
    sp = grid.get("source_params", {})
    keys = list(sp.keys())
    value_lists = [sp[k] for k in keys]
    filter_sets = grid.get("filter_sets", [[]])
    combos = []
    for values in itertools.product(*value_lists) if keys else [()]:
        source_params = dict(zip(keys, values))
        for fset in filter_sets:
            combos.append({"source_params": source_params, "filters": fset})
    return combos


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)

    raw = yaml.safe_load(Path(args.config).read_text())
    strategy = raw["strategy"]
    base = raw.get("base", {})
    grid = raw["grid"]
    symbols = raw["symbols"]
    interval = raw.get("interval", "1d")
    cash = float(raw.get("starting_cash", 40_000.0))
    hw = int(raw.get("history_window", 250))
    benchmark = raw.get("benchmark")
    dev, holdout = raw["dev"], raw["holdout"]
    objective = raw.get("objective", "median_return")
    min_trades = int(raw.get("min_trades", 30))

    combos = _grid_combos(grid)
    print(f"\nOOS SEARCH  strategy={strategy}  source={base.get('source')}")
    print(f"  {len(combos)} combos × {len(symbols)} symbols on DEV "
          f"({str(dev['start'])[:10]}..{str(dev['end'])[:10]})")
    print(f"  objective={objective}  min_trades={min_trades}\n")

    results = []
    for i, combo in enumerate(combos):
        params = {**base, "source_params": combo["source_params"], "filters": combo["filters"]}
        agg = _basket_agg(strategy, params, symbols, dev, interval, cash, hw, benchmark)
        score = agg.get(objective, 0.0)
        ok = agg["total_trades"] >= min_trades
        fdesc = ",".join(f["name"] for f in combo["filters"]) or "none"
        print(f"  [{i+1:>2}/{len(combos)}] {combo['source_params']} filters=[{fdesc}]  "
              f"dev {objective}={_pct(score) if 'return' in objective or 'rate' in objective or 'profitable' in objective else f'{score:.2f}'}  "
              f"trades={agg['total_trades']}{'' if ok else '  (skip<min)'}")
        if ok:
            results.append((score, combo, params, agg))

    if not results:
        print("\nNo combo cleared min_trades. Widen the grid or lower min_trades.")
        return 1

    # Best on DEV (we optimized here — this number is inflated by selection).
    best_score, best_combo, best_params, best_dev = max(results, key=lambda x: x[0])
    fdesc = ",".join(f["name"] for f in best_combo["filters"]) or "none"

    # The honest read: same config, UNTOUCHED holdout window.
    hold = _basket_agg(strategy, best_params, symbols, holdout, interval, cash, hw, benchmark)

    print("\n" + "=" * 64)
    print(f"  BEST ON DEV: {best_combo['source_params']}  filters=[{fdesc}]")
    print("  " + "-" * 60)
    print(f"  {'':18}{'DEV (optimized)':>16}{'HOLDOUT (honest)':>18}")
    for k in ("mean_return", "median_return", "pct_profitable", "mean_win_rate"):
        print(f"  {k:18}{_pct(best_dev[k]):>16}{_pct(hold[k]):>18}")
    print(f"  {'$/trade':18}{best_dev['mean_trade_pnl']:>16.0f}{hold['mean_trade_pnl']:>18.0f}")
    print(f"  {'avg_holding_days':18}{best_dev['avg_holding_days']:>16.0f}{hold['avg_holding_days']:>18.0f}")
    print(f"  {'trades (/sym)':18}{best_dev['total_trades']:>10} (~{best_dev['trades_per_symbol']:.0f}){hold['total_trades']:>12} (~{hold['trades_per_symbol']:.0f})")
    print(f"  {'worst_dd':18}{_pct(best_dev['worst_dd']):>16}{_pct(hold['worst_dd']):>18}")
    print("=" * 64)
    print("  Trust the HOLDOUT column. A large dev→holdout drop = overfit.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
