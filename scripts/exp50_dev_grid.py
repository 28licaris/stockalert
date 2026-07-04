"""
EXP-50 H-39-dev: cost-efficient gap-hold grid on the DEV window.

Runs hourly_swing(gap_hold) through the real engine (costs included)
over g x h on 2006-2018 — SELECTION ONLY (no significance claims; the
single net-Sharpe winner goes to the untouched holdout Tier-2). All 12
results printed and saved.

  poetry run python scripts/exp50_dev_grid.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sim.backtester import Backtester  # noqa: E402
from app.services.sim.loader import build_strategy  # noqa: E402
from app.services.sim.schemas import BacktestConfig  # noqa: E402
from scripts.research_bars import load_bar_lists  # noqa: E402

SNAPSHOT = "data/mcpt/swing40_hourly_2006_2018.parquet"
GRID = [(g, h) for g in (0.01, 0.015, 0.02, 0.03) for h in (1, 3, 5)]


def main() -> int:
    bars = load_bar_lists(SNAPSHOT, align=True)
    results = []
    for g, h in GRID:
        cfg = BacktestConfig(
            symbols=sorted(bars), start="2006-01-01T00:00:00Z",
            end="2018-12-31T23:59:59Z", interval="1h", starting_cash=100_000.0,
            history_window=30, max_concurrent_positions=8,
            fees_model="zero", slippage_model="percent",
            slippage_params={"pct": 0.0005},
        )
        bt = Backtester()
        bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
        bt._fetch_bars_multi = lambda *a, **k: {"1h": bars}  # type: ignore[assignment]
        bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
        strat = build_strategy("hourly_swing", {
            "trigger": "gap_hold", "gap_min": g,
            "hold_bars": 7 * h, "position_size_pct": 0.12,
        }, "1h")
        res = bt.run_portfolio(strat, cfg)
        m = res.metrics
        closing = sum(1 for t in res.trades if t.is_closing)
        row = {"g": g, "h": h, "sharpe": m.sharpe_ratio, "total_return": m.total_return,
               "pf": m.profit_factor, "max_dd": m.max_drawdown, "round_trips": closing}
        results.append(row)
        print(f"g={g:.3f} h={h}  sharpe={m.sharpe_ratio:+.3f}  "
              f"ret={m.total_return*100:+7.1f}%  PF={m.profit_factor or float('nan'):.3f}  "
              f"DD={m.max_drawdown*100:+.1f}%  trades={closing}", flush=True)

    results.sort(key=lambda r: (r["sharpe"] if r["sharpe"] is not None else -9), reverse=True)
    w = results[0]
    print(f"\nDEV WINNER (net Sharpe): g={w['g']} h={w['h']}  "
          f"sharpe={w['sharpe']:+.3f}  PF={w['pf']:.3f}")
    Path("data/mcpt/exp50_dev_grid.json").write_text(json.dumps(results, indent=2))
    print("saved data/mcpt/exp50_dev_grid.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
