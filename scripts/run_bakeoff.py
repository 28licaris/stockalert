#!/usr/bin/env python3
"""
Run-history bake-off — print a side-by-side comparison of
recent backtest runs from the `agent_runs` registry.

Usage:
    poetry run python scripts/run_bakeoff.py
    poetry run python scripts/run_bakeoff.py --strategies sma_crossover,ema_crossover
    poetry run python scripts/run_bakeoff.py --symbols AAPL --interval 1d --limit 20

The CLI reads from `agent_runs` (no recomputation). Re-running
backtests to refresh the table is done via
`scripts/run_backtest.py --config <yaml>` for each strategy
(reproducibility: the same config produces the same metrics row,
so re-running is safe but unnecessary if the row already exists).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Optional

# Make `app` importable when run as a top-level script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sim.registry import list_runs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compare recent backtest runs.")
    p.add_argument(
        "--strategies",
        default="sma_crossover,ema_crossover,rsi_reversion,bollinger_mean_revert,llm_agent",
        help="Comma-separated strategy names to compare. Default: all baselines + llm_agent.",
    )
    p.add_argument(
        "--limit-per-strategy",
        type=int,
        default=1,
        help="How many most-recent runs to show per strategy. Default: 1 (latest only).",
    )
    p.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbol filter (only rows where ALL of these are in `symbols`).",
    )
    p.add_argument(
        "--interval",
        default=None,
        help="Filter to one bar interval ('1d', '1m', etc).",
    )
    args = p.parse_args(argv)

    strategy_names = [s.strip() for s in args.strategies.split(",") if s.strip()]
    symbol_filter: Optional[set[str]] = (
        {s.strip().upper() for s in args.symbols.split(",")} if args.symbols else None
    )

    rows: list[dict] = []
    for sname in strategy_names:
        # Pull a few extra so we have headroom to filter; trim per-strategy after.
        candidates = list_runs(strategy_name=sname, limit=args.limit_per_strategy * 5)
        kept_for_strategy = 0
        for r in candidates:
            if kept_for_strategy >= args.limit_per_strategy:
                break
            if symbol_filter is not None:
                if not symbol_filter <= set(r.get("symbols") or []):
                    continue
            if args.interval is not None and r.get("interval") != args.interval:
                continue
            rows.append(r)
            kept_for_strategy += 1

    if not rows:
        print("No rows in agent_runs matched. Run a backtest first:")
        print("  poetry run python scripts/run_backtest.py --config configs/canary.yaml")
        return 1

    _print_table(rows)
    return 0


def _print_table(rows: Iterable[dict]) -> None:
    headers = [
        ("strategy", 22),
        ("symbols", 14),
        ("interval", 8),
        ("start", 11),
        ("end", 11),
        ("trades", 7),
        ("return", 10),
        ("sharpe", 8),
        ("max_dd", 8),
        ("final_equity", 14),
    ]
    sep = "─" * (sum(w for _, w in headers) + len(headers) * 3 - 1)
    print()
    print(sep)
    print(" │ ".join(f"{name:^{w}}" for name, w in headers))
    print(sep)

    for r in rows:
        symbols = ",".join(r.get("symbols") or [])
        if len(symbols) > 14:
            symbols = symbols[:11] + "..."
        ret = float(r.get("total_return") or 0)
        sharpe = float(r.get("sharpe_ratio") or 0)
        max_dd = float(r.get("max_drawdown") or 0)
        equity = float(r.get("final_equity") or 0)
        row = [
            (str(r.get("strategy_name", "")), 22),
            (symbols, 14),
            (str(r.get("interval", "")), 8),
            (str(r.get("start_date", ""))[:10], 11),
            (str(r.get("end_date", ""))[:10], 11),
            (str(r.get("n_trades", 0)), 7),
            (f"{ret * 100:+.2f}%", 10),
            (f"{sharpe:+.3f}", 8),
            (f"{max_dd * 100:+.2f}%", 8),
            (f"${equity:,.2f}", 14),
        ]
        print(" │ ".join(f"{v:>{w}}" for v, w in row))
    print(sep)
    print()


if __name__ == "__main__":
    sys.exit(main())
