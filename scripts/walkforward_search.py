"""
Walk-forward combination search over the dynamic-universe portfolio.

The disciplined, data-appropriate alternative to RL: sweep a small grid of
*interpretable* knobs (momentum top-N, lookback, confluence stack) and score each
config on per-year walk-forward windows. Select on the DEV years (2022-2023) and
report the untouched HOLDOUT years (2024-2025) — so the winner is chosen without
peeking at its holdout score.

Efficiency: ClickHouse bars for the whole pool are loaded ONCE (full range) and
sliced in-memory per window, so the grid runs without re-hitting CH per config.

Usage: poetry run python scripts/walkforward_search.py
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

POOL = ("AAPL ABBV ABNB ABT ACWI ADBE AFRM AMAT AMC AMD AMZN ARM ASML AVDV AVGO BA "
        "BABA BAC BILL BLK BMY BNTX C CAT CLSK COIN COP COPX COST CRM CRWD CSCO CVX "
        "DASH DBA DBC DDOG DIA EEM EFA F GE GLD GME GOOGL GS HD HOOD HYG IAU IBM INTC "
        "IWM JNJ JPM KLAC KO LCID LLY LMT LQD LRCX LYFT MA MCD META MRK MRVL MS MSFT "
        "MU NET NFLX NKE NVDA NVTS ORCL PALL PINS PLTR PPLT QCOM QQQ RBLX RIOT RIVN "
        "RTX SBUX SHY SLV SNAP SOFI SOXX SPY T TLT TSLA TSM TXN UNG UNH USO V VOO VTI "
        "VXUS WFC WMT XLB XLE XLF XLI XLK XLP XLRE XLU XLV XLY XOM").split()

UTC = timezone.utc
FULL_START = datetime(2022, 1, 1, tzinfo=UTC)
FULL_END = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
YEARS = [2022, 2023, 2024, 2025]
DEV_YEARS, HOLDOUT_YEARS = [2022, 2023], [2024, 2025]

RS = {"name": "relative_strength", "params": {"lookback": 60}}
ADX = {"name": "adx", "params": {"threshold": 20}}

# Interpretable grid: momentum selection breadth × lookback × confluence stack.
GRID = [
    {"top_n": tn, "lookback": lb, "filters": flt, "fname": fn}
    for tn in (10, 15, 20)
    for lb in (60, 90)
    for (flt, fn) in (([], "none"), ([RS], "rs"))
]


def _cfg(top_n, lookback, start, end):
    return BacktestConfig(
        symbols=list(POOL), start=start, end=end, interval="1d", benchmark="SPY",
        starting_cash=100_000.0, history_window=300, max_concurrent_positions=10,
        max_portfolio_heat=0.12, momentum_top_n=top_n, momentum_lookback=lookback,
    )


def _strategy(filters):
    return build_strategy("alert_driven", {
        "source": "breakout",
        "source_params": {"lookback": 20, "vol_mult": 1.5, "reward_risk_mult": 3.0},
        "filters": filters, "filter_mode": "all",
        "risk_pct": 0.01, "max_risk_pct": 0.05, "min_reward_risk": 0.0,
    }, interval="1d")


def main() -> int:
    bt = Backtester()
    full_cfg = _cfg(15, 60, FULL_START, FULL_END)
    print(f"loading {len(POOL)} symbols from ClickHouse (once)…", flush=True)
    FULL = bt._fetch_bars_multi(full_cfg, ["1d"])
    BENCH = bt._load_benchmark(full_cfg, "1d") if full_cfg.benchmark else None
    n_bars = sum(len(v) for v in FULL["1d"].values())
    print(f"  loaded {n_bars:,} daily bars across {len(FULL['1d'])} symbols\n", flush=True)

    def _slice(config, intervals):
        s, e = config.start.date(), config.end.date()
        return {iv: {sym: [b for b in bars if s <= b.timestamp.date() <= e]
                     for sym, bars in FULL[iv].items()} for iv in intervals}

    bt._fetch_bars_multi = _slice          # type: ignore[assignment]
    bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
    if BENCH is not None:
        bt._load_benchmark = lambda config, interval: BENCH  # type: ignore[assignment]

    results = []
    for i, g in enumerate(GRID, 1):
        per_year = {}
        for yr in YEARS:
            cfg = _cfg(g["top_n"], g["lookback"],
                       datetime(yr, 1, 1, tzinfo=UTC), datetime(yr, 12, 31, 23, 59, 59, tzinfo=UTC))
            res = bt.run_portfolio(_strategy(g["filters"]), cfg)
            m = res.metrics
            per_year[yr] = (m.total_return, m.sharpe_ratio or 0.0, m.profit_factor or 0.0)
        dev = mean(per_year[y][1] for y in DEV_YEARS)        # mean DEV Sharpe (selection)
        hold = mean(per_year[y][1] for y in HOLDOUT_YEARS)   # mean HOLDOUT Sharpe (report)
        worst = min(per_year[y][0] for y in YEARS)           # worst single-year return
        results.append({**g, "per_year": per_year, "dev": dev, "hold": hold, "worst": worst})
        tag = f"top{g['top_n']}/lb{g['lookback']}/{g['fname']}"
        print(f"[{i:>2}/{len(GRID)}] {tag:<20} "
              f"yr%=[{' '.join(f'{per_year[y][0]*100:+5.0f}' for y in YEARS)}] "
              f"devSharpe={dev:+.2f} holdSharpe={hold:+.2f} worstYr={worst*100:+.0f}%", flush=True)

    # Select by DEV Sharpe (no peeking at holdout); report the winner's holdout.
    results.sort(key=lambda r: r["dev"], reverse=True)
    w = results[0]
    print("\n=== selected by DEV (2022-23) mean Sharpe ===")
    print(f"  WINNER: top_n={w['top_n']} lookback={w['lookback']} filters={w['fname']}")
    print(f"    per-year return: " + "  ".join(f"{y}:{w['per_year'][y][0]*100:+.1f}%" for y in YEARS))
    print(f"    per-year Sharpe: " + "  ".join(f"{y}:{w['per_year'][y][1]:+.2f}" for y in YEARS))
    print(f"    per-year PF:     " + "  ".join(f"{y}:{w['per_year'][y][2]:.2f}" for y in YEARS))
    print(f"    DEV Sharpe (22-23)={w['dev']:+.2f}  HOLDOUT Sharpe (24-25)={w['hold']:+.2f}  worst year={w['worst']*100:+.1f}%")
    print("\n  (DEV-selected winner's HOLDOUT performance is the honest OOS read.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
