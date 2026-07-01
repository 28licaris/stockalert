"""Run a strategy as a single shared-capital PORTFOLIO across a basket, with
risk caps. The realistic equity-curve / drawdown the per-symbol sweep can't show.

Usage: poetry run python scripts/run_portfolio.py --config configs/portfolio_regime.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app.services.sim.backtester import Backtester  # noqa: E402
from app.services.sim.schemas import BacktestConfig  # noqa: E402
from scripts.run_backtest import _load_strategy  # noqa: E402


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--start", default=None, help="Override config start (walk-forward).")
    ap.add_argument("--end", default=None, help="Override config end (walk-forward).")
    a = ap.parse_args(argv)
    r = yaml.safe_load(Path(a.config).read_text())
    cfg = BacktestConfig(
        symbols=r["symbols"], start=a.start or r["start"], end=a.end or r["end"],
        interval=r.get("interval", "1d"), starting_cash=float(r.get("starting_cash", 100_000)),
        history_window=int(r.get("history_window", 250)), benchmark=r.get("benchmark"),
        max_concurrent_positions=int(r.get("max_concurrent_positions", 10)),
        max_portfolio_heat=float(r.get("max_portfolio_heat", 0.10)),
        momentum_top_n=r.get("momentum_top_n"),
        momentum_bottom_n=r.get("momentum_bottom_n"),
        momentum_lookback=int(r.get("momentum_lookback", 60)),
        daily_table=r.get("daily_table"),
        ranked_admission=bool(r.get("ranked_admission", False)),
    )
    strat = _load_strategy(r["strategy"], r.get("strategy_params", {}), interval=cfg.interval)
    res = Backtester().run_portfolio(strat, cfg)
    m = res.metrics
    closing = sum(1 for t in res.trades if t.is_closing)
    print(f"\nPORTFOLIO  {r['strategy']}  {len(cfg.symbols)} symbols  "
          f"{str(cfg.start)[:10]}..{str(cfg.end)[:10]}")
    print(f"  max_concurrent={cfg.max_concurrent_positions}  heat_cap={cfg.max_portfolio_heat:.0%}  "
          f"start_cash=${cfg.starting_cash:,.0f}")
    print("  " + "-" * 50)
    print(f"  total return     {m.total_return * 100:+8.1f}%")
    print(f"  annualized       {(m.annualized_return or 0) * 100:+8.1f}%")
    print(f"  Sharpe           {m.sharpe_ratio if m.sharpe_ratio is not None else float('nan'):8.2f}")
    print(f"  max drawdown     {m.max_drawdown * 100:+8.1f}%")
    print(f"  win rate         {(m.win_rate or 0) * 100:8.1f}%")
    print(f"  profit factor    {m.profit_factor if m.profit_factor is not None else float('nan'):8.2f}")
    print(f"  round trips      {closing:8d}")
    print(f"  avg hold (days)  {(m.avg_holding_days or 0):8.1f}")
    print(f"  final equity     ${m.final_equity:,.0f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
