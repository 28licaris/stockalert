"""
Engine equivalence fingerprint — the correctness gate for performance work.

Runs two pinned reference backtests (mean-revert stop-less path + breakout
momentum/risk-manager path) and reduces every trade and metric to one
SHA-256. Any engine optimization must reproduce the SAME hash: identical
trades, fills, fees, and metrics — not "close", identical.

  # before optimizing:
  poetry run python scripts/dev/engine_fingerprint.py --save data/mcpt/engine_fp_baseline.json
  # after each change:
  poetry run python scripts/dev/engine_fingerprint.py --check data/mcpt/engine_fp_baseline.json

Bars are pinned via a bar snapshot (research_bars.py) so the fingerprint
is independent of live ClickHouse writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.sim.backtester import Backtester  # noqa: E402
from app.services.sim.loader import build_strategy  # noqa: E402
from app.services.sim.schemas import BacktestConfig  # noqa: E402
from scripts.research_bars import load_bar_lists  # noqa: E402

UTC = timezone.utc

SYMBOLS = ("AAPL MSFT NVDA AMZN GOOGL META JPM XOM UNH WMT LLY AVGO COST HD ABBV "
           "MRK PEP KO BAC CRM AMD NFLX INTC DIS CSCO QCOM TXN CAT GE BA SPY QQQ "
           "IWM XLE XLF GLD TLT COIN PLTR MSTR ROKU SNAP UBER ABNB DDOG NET SQ "
           "SHOP ZM DOCU").split()
START, END = datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC)

CASES = {
    "meanrev_stopless": (
        "rsi_reversion",
        {"rsi_period": 4, "oversold_threshold": 10, "exit_threshold": 50,
         "position_size_pct": 0.05, "rsi_kind": "wilder"},
        {"fees_model": "zero", "slippage_model": "percent",
         "slippage_params": {"pct": 0.0005}, "max_concurrent_positions": 20},
    ),
    "breakout_momentum_brake": (
        "alert_driven",
        {"source": "breakout",
         "source_params": {"lookback": 20, "vol_mult": 1.5, "reward_risk_mult": 3.0},
         "filters": [], "filter_mode": "all", "risk_pct": 0.01,
         "max_risk_pct": 0.05, "min_reward_risk": 0.0},
        {"momentum_top_n": 15, "momentum_lookback": 60, "dd_brake_limit": 0.15,
         "max_concurrent_positions": 10, "max_portfolio_heat": 0.12},
    ),
}


def _fingerprint(bars_path: str) -> dict:
    bars = load_bar_lists(bars_path, symbols=list(SYMBOLS), start=START, end=END)
    out = {}
    for case, (strategy, sparams, cfg_extra) in CASES.items():
        cfg = BacktestConfig(
            symbols=list(SYMBOLS), start=START, end=END, interval="1d",
            starting_cash=100_000.0, history_window=300, benchmark="SPY",
            **cfg_extra,
        )
        bt = Backtester()
        bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
        bt._fetch_bars_multi = lambda *a, **k: {"1d": bars}  # type: ignore[assignment]
        bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
        res = bt.run_portfolio(build_strategy(strategy, sparams, "1d"), cfg)
        payload = {
            "trades": [
                [t.symbol, t.side, round(t.quantity, 9), round(t.price, 9),
                 t.timestamp.isoformat(), round(t.fees, 9), round(t.realized_pnl, 9)]
                for t in res.trades
            ],
            "equity_last": round(res.metrics.final_equity, 6),
            "metrics": {
                k: (round(v, 12) if isinstance(v, float) else v)
                for k, v in res.metrics.model_dump().items()
            },
        }
        blob = json.dumps(payload, sort_keys=True)
        out[case] = {
            "sha256": hashlib.sha256(blob.encode()).hexdigest(),
            "n_trades": len(payload["trades"]),
            "final_equity": payload["equity_last"],
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bars", default="data/mcpt/engine_fp_bars.parquet",
                    help="pinned bar snapshot (created via research_bars.py export)")
    ap.add_argument("--save", help="write fingerprint JSON here (baseline)")
    ap.add_argument("--check", help="compare against a saved baseline; exit 1 on drift")
    a = ap.parse_args(argv)
    fp = _fingerprint(a.bars)
    for case, v in fp.items():
        print(f"{case}: {v['n_trades']} trades  final_equity={v['final_equity']:.2f}  "
              f"sha={v['sha256'][:16]}…", flush=True)
    if a.save:
        Path(a.save).write_text(json.dumps(fp, indent=2))
        print(f"baseline saved -> {a.save}")
    if a.check:
        base = json.loads(Path(a.check).read_text())
        drift = {c for c in base if base[c]["sha256"] != fp.get(c, {}).get("sha256")}
        if drift:
            print(f"FINGERPRINT DRIFT in: {sorted(drift)} — the change altered behavior")
            return 1
        print("fingerprint MATCHES baseline — behavior preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
