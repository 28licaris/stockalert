"""
Improve the momentum strategy WITHOUT cheating.

Hard data wall:
  TRAIN  = 2022-2023  → used to TUNE / pick the winner.
  HOLDOUT= 2024-2025  → NEVER used for selection; only reported. The honest test.

We search improvement knobs (selection breadth, breakout lookback, time-stop,
reward:risk) on TRAIN only, pick the best by TRAIN Sharpe, then reveal its HOLDOUT.
A change counts as a real improvement ONLY if it also beats the current baseline on
the HOLDOUT — otherwise it's overfit and we keep the baseline. (The live/simulated
paper record is a separate, post-go-live window that tuning never touches.)

Bars are loaded once from ClickHouse and sliced in-memory.

Usage: poetry run python scripts/improve_strategy.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sim.backtester import Backtester       # noqa: E402
from app.services.sim.loader import build_strategy        # noqa: E402
from app.services.sim.schemas import BacktestConfig       # noqa: E402
from scripts.walkforward_search import POOL               # noqa: E402

UTC = timezone.utc
FULL_START = datetime(2022, 1, 1, tzinfo=UTC)
FULL_END = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
TRAIN_YEARS = [2022, 2023]     # tune on these
HOLDOUT_YEARS = [2024, 2025]   # never selected on — the honest test

BASELINE = {"top_n": 15, "lookback": 60, "bo_lb": 20, "rr": 3.0, "max_hold": None}

# Improvement grid (interpretable knobs around the current winner).
GRID = [
    {"top_n": tn, "lookback": 60, "bo_lb": bo, "rr": 3.0, "max_hold": mh}
    for tn in (12, 15, 18)
    for bo in (20, 40)
    for mh in (None, 45)
]


def _cfg(g, start, end):
    return BacktestConfig(
        symbols=list(POOL), start=start, end=end, interval="1d", benchmark="SPY",
        starting_cash=100_000.0, history_window=300, max_concurrent_positions=10,
        max_portfolio_heat=0.12, momentum_top_n=g["top_n"], momentum_lookback=g["lookback"],
    )


def _strategy(g):
    return build_strategy("alert_driven", {
        "source": "breakout",
        "source_params": {"lookback": g["bo_lb"], "vol_mult": 1.5, "reward_risk_mult": g["rr"]},
        "filters": [], "filter_mode": "all",
        "risk_pct": 0.01, "max_risk_pct": 0.05, "min_reward_risk": 0.0,
        "max_holding_days": g["max_hold"],
    }, interval="1d")


def _evaluate(bt, g) -> dict:
    per_year = {}
    for yr in TRAIN_YEARS + HOLDOUT_YEARS:
        cfg = _cfg(g, datetime(yr, 1, 1, tzinfo=UTC), datetime(yr, 12, 31, 23, 59, 59, tzinfo=UTC))
        m = bt.run_portfolio(_strategy(g), cfg).metrics
        per_year[yr] = (m.total_return, m.sharpe_ratio or 0.0, m.profit_factor or 0.0)
    return {
        **g, "per_year": per_year,
        "train_sharpe": mean(per_year[y][1] for y in TRAIN_YEARS),
        "hold_sharpe": mean(per_year[y][1] for y in HOLDOUT_YEARS),
        "hold_return": sum(per_year[y][0] for y in HOLDOUT_YEARS),
    }


def _tag(g):
    return f"top{g['top_n']}/bo{g['bo_lb']}/hold{g['max_hold'] or '-'}/rr{g['rr']:.0f}"


def main() -> int:
    bt = Backtester()
    full_cfg = _cfg(BASELINE, FULL_START, FULL_END)
    print(f"loading {len(POOL)} symbols once…", flush=True)
    FULL = bt._fetch_bars_multi(full_cfg, ["1d"])
    BENCH = bt._load_benchmark(full_cfg, "1d")
    print(f"  {sum(len(v) for v in FULL['1d'].values()):,} daily bars\n", flush=True)

    def _slice(config, intervals):
        s, e = config.start.date(), config.end.date()
        return {iv: {sym: [b for b in bars if s <= b.timestamp.date() <= e]
                     for sym, bars in FULL[iv].items()} for iv in intervals}
    bt._fetch_bars_multi = _slice                     # type: ignore[assignment]
    bt._capture_snapshot = lambda *a, **k: None       # type: ignore[assignment]
    bt._load_benchmark = lambda config, interval: BENCH  # type: ignore[assignment]

    base = _evaluate(bt, BASELINE)
    print(f"BASELINE {_tag(BASELINE):<24} trainSharpe {base['train_sharpe']:+.2f}  "
          f"holdSharpe {base['hold_sharpe']:+.2f}  holdRet {base['hold_return']*100:+.0f}%\n", flush=True)

    results = []
    for i, g in enumerate(GRID, 1):
        r = _evaluate(bt, g)
        results.append(r)
        print(f"[{i:>2}/{len(GRID)}] {_tag(g):<24} trainSharpe {r['train_sharpe']:+.2f}  "
              f"holdSharpe {r['hold_sharpe']:+.2f}  holdRet {r['hold_return']*100:+.0f}%", flush=True)

    # Select STRICTLY by TRAIN Sharpe — holdout was never consulted for the pick.
    winner = max(results, key=lambda r: r["train_sharpe"])
    print(f"\n=== TRAIN-selected winner: {_tag(winner)} ===")
    print(f"  train Sharpe {winner['train_sharpe']:+.2f} (selection)")
    print(f"  HOLDOUT Sharpe {winner['hold_sharpe']:+.2f}  return {winner['hold_return']*100:+.0f}%  (honest test)")
    beats = winner["hold_sharpe"] > base["hold_sharpe"] and winner["hold_return"] > base["hold_return"]
    if _tag(winner) == _tag(BASELINE):
        print("  → winner IS the baseline. No change.")
    elif beats:
        print(f"  → REAL IMPROVEMENT: beats baseline on the untouched holdout "
              f"(Sharpe {base['hold_sharpe']:+.2f}→{winner['hold_sharpe']:+.2f}, "
              f"ret {base['hold_return']*100:+.0f}%→{winner['hold_return']*100:+.0f}%). Ship it.")
    else:
        print(f"  → OVERFIT: won on train but does NOT beat baseline on holdout "
              f"(base holdSharpe {base['hold_sharpe']:+.2f}, ret {base['hold_return']*100:+.0f}%). KEEP baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
