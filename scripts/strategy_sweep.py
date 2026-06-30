"""
Strategy sweep — run one strategy across a BASKET of symbols and one or more
time WINDOWS, then aggregate. The per-symbol read tells you whether an edge
generalizes or was one lucky name; the multi-window read is the cheap
generalization check (does it hold up in a period it wasn't chosen on).

This is R&D tooling, not production. It runs each (symbol, window) as an
independent single-symbol backtest via the same Backtester the CLI uses, so
results are directly comparable to `run_backtest.py`.

Usage:
    poetry run python scripts/strategy_sweep.py --config configs/sweep_breakout.yaml

Config shape (YAML):
    strategy: alert_driven
    strategy_params: { source: breakout, source_params: {...}, risk_pct: 0.01 }
    symbols: [AAPL, MSFT, NVDA, ...]
    interval: 1d
    starting_cash: 40000
    history_window: 250
    windows:                       # one or more; label is free-form
      - { label: "2022-2023", start: 2022-01-01T00:00:00Z, end: 2023-12-31T23:59:59Z }
      - { label: "2024-2025", start: 2024-01-01T00:00:00Z, end: 2025-12-31T23:59:59Z }
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app.services.sim.backtester import Backtester  # noqa: E402
from app.services.sim.schemas import BacktestConfig  # noqa: E402
from scripts.run_backtest import _load_strategy  # noqa: E402


def _run_one(
    strategy_name: str, params: dict[str, Any], symbol: str,
    start: str, end: str, interval: str, cash: float, hw: int,
) -> Optional[dict[str, Any]]:
    """One single-symbol backtest. Returns a metrics row, or None on no-data/error."""
    cfg = BacktestConfig(
        symbols=[symbol], start=start, end=end, interval=interval,
        starting_cash=cash, history_window=hw,
    )
    strat = _load_strategy(strategy_name, params, interval=interval)
    try:
        res = Backtester().run(strat, cfg)
    except Exception as exc:  # noqa: BLE001 — research tool; record + continue
        print(f"    {symbol:6}  ERROR: {type(exc).__name__}: {exc}")
        return None
    m = res.metrics
    return {
        "symbol": symbol, "return": m.total_return, "win_rate": m.win_rate,
        "profit_factor": m.profit_factor, "n_trades": m.n_trades,
        "max_dd": m.max_drawdown, "sharpe": m.sharpe_ratio,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    traded = [r for r in rows if r["n_trades"] > 0]
    rets = [r["return"] for r in traded]
    wins = [r["win_rate"] for r in traded if r["win_rate"] is not None]
    return {
        "symbols_total": len(rows),
        "symbols_traded": len(traded),
        "symbols_no_trades": len(rows) - len(traded),
        "total_trades": sum(r["n_trades"] for r in rows),
        "mean_return": statistics.mean(rets) if rets else 0.0,
        "median_return": statistics.median(rets) if rets else 0.0,
        "pct_profitable": (sum(1 for r in rets if r > 0) / len(rets)) if rets else 0.0,
        "mean_win_rate": statistics.mean(wins) if wins else 0.0,
        "worst_dd": min((r["max_dd"] for r in traded), default=0.0),
    }


def _pct(x: Optional[float]) -> str:
    return "   n/a" if x is None else f"{x * 100:+6.1f}%"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)

    raw = yaml.safe_load(Path(args.config).read_text())
    strategy = raw["strategy"]
    params = raw.get("strategy_params", {})
    symbols = raw["symbols"]
    interval = raw.get("interval", "1d")
    cash = float(raw.get("starting_cash", 40_000.0))
    hw = int(raw.get("history_window", 250))
    windows = raw.get("windows") or [{"label": "full", "start": raw["start"], "end": raw["end"]}]

    print(f"\nSWEEP  strategy={strategy}  params={params}")
    print(f"       {len(symbols)} symbols × {len(windows)} window(s)\n")

    for w in windows:
        label, start, end = w["label"], w["start"], w["end"]
        print(f"── window {label}  ({start} .. {end}) " + "─" * 20)
        print(f"    {'symbol':6}  {'return':>7}  {'win':>6}  {'PF':>6}  {'trades':>6}  {'maxDD':>7}")
        rows: list[dict[str, Any]] = []
        for sym in symbols:
            r = _run_one(strategy, params, sym, start, end, interval, cash, hw)
            if r is None:
                continue
            rows.append(r)
            pf = "  n/a" if r["profit_factor"] is None else f"{r['profit_factor']:6.2f}"
            print(f"    {sym:6}  {_pct(r['return'])}  {_pct(r['win_rate'])}  {pf}  "
                  f"{r['n_trades']:6d}  {_pct(r['max_dd'])}")
        agg = _aggregate(rows)
        print(f"    {'─' * 50}")
        print(f"    AGG  mean {_pct(agg['mean_return'])}  median {_pct(agg['median_return'])}  "
              f"profitable {agg['pct_profitable'] * 100:.0f}%  "
              f"win {_pct(agg['mean_win_rate'])}  trades {agg['total_trades']}  "
              f"worstDD {_pct(agg['worst_dd'])}  "
              f"({agg['symbols_traded']}/{agg['symbols_total']} traded)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
