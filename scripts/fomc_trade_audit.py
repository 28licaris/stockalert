"""
FOMC paper-strategy trade audit (the "GPT review" items, 2026-07-04).

Reproduces both LOCKED Tier-2 configs trade-by-trade and emits the
transparency tables an external reviewer asked for:
  - per-trade contribution (top-5 winners' share of profit, worst 5)
  - per-year P&L + robustness exclusions (ex-2020, ex-2022,
    ex-best-year, ex-best-trade)
  - win rate / avg win / avg loss / median trade
  - SEP vs non-SEP meeting split (Mar/Jun/Sep/Dec = projections+dots)
  - hourly timestamp proof: every exit fill strictly before 14:00 ET
    on the announcement day (the existential check)

  poetry run python scripts/fomc_trade_audit.py
Writes data/mcpt/fomc_trade_audit.json.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sim.backtester import Backtester  # noqa: E402
from app.services.sim.loader import build_strategy  # noqa: E402
from app.services.sim.strategies.fomc_calendar import FOMC_ANNOUNCEMENT_DATES  # noqa: E402
from scripts.mcpt_walkforward import _build_cfg  # noqa: E402
from scripts.research_bars import load_bar_lists  # noqa: E402

ET = ZoneInfo("America/New_York")
HOURLY_SNAPSHOT = "data/mcpt/spy_hourly_2019_2026.parquet"
ANNOUNCEMENTS = sorted(date.fromisoformat(d) for d in FOMC_ANNOUNCEMENT_DATES)


def _et(ts):
    if ts.tzinfo is None:  # naive = UTC by platform convention
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET)


def _round_trips(trades) -> list[dict]:
    """Pair buy legs with their closing sells (1 position at a time)."""
    out, entry = [], None
    for t in trades:
        if t.side == "buy":
            entry = t
        elif t.is_closing and entry is not None:
            ann = next((a for a in ANNOUNCEMENTS
                        if 0 <= (a - _et(t.timestamp).date()).days <= 5), None)
            out.append({
                "entry_ts_et": _et(entry.timestamp).isoformat(),
                "exit_ts_et": _et(t.timestamp).isoformat(),
                "entry_px": entry.price, "exit_px": t.price,
                "qty": t.quantity, "pnl": t.realized_pnl,
                "ret_pct": t.realized_pnl / (entry.price * entry.quantity) * 100,
                "year": _et(t.timestamp).year,
                "announcement": ann.isoformat() if ann else None,
                "sep_meeting": ann.month in (3, 6, 9, 12) if ann else None,
            })
            entry = None
    return out


def _analytics(rts: list[dict]) -> dict:
    pnl = [r["pnl"] for r in rts]
    total = sum(pnl)
    wins = sorted((r for r in rts if r["pnl"] > 0), key=lambda r: -r["pnl"])
    losses = sorted((r for r in rts if r["pnl"] <= 0), key=lambda r: r["pnl"])
    by_year: dict[int, float] = defaultdict(float)
    for r in rts:
        by_year[r["year"]] += r["pnl"]
    best_year = max(by_year, key=by_year.get)
    med = sorted(pnl)[len(pnl) // 2]
    sep = [r for r in rts if r["sep_meeting"]]
    nonsep = [r for r in rts if r["sep_meeting"] is False]
    return {
        "n_trades": len(rts), "total_pnl": total,
        "win_rate": len(wins) / len(rts),
        "avg_win": sum(r["pnl"] for r in wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(r["pnl"] for r in losses) / len(losses) if losses else 0.0,
        "median_trade": med,
        "top5_winners": [{k: r[k] for k in ("announcement", "pnl", "ret_pct")} for r in wins[:5]],
        "top5_pnl_share_of_total": sum(r["pnl"] for r in wins[:5]) / total if total else None,
        "worst5_losers": [{k: r[k] for k in ("announcement", "pnl", "ret_pct")} for r in losses[:5]],
        "pnl_by_year": {str(y): round(v, 2) for y, v in sorted(by_year.items())},
        "ex_2020": total - by_year.get(2020, 0.0),
        "ex_2022": total - by_year.get(2022, 0.0),
        "ex_best_year": total - by_year[best_year], "best_year": best_year,
        "ex_best_trade": total - (wins[0]["pnl"] if wins else 0.0),
        "sep_meetings": {"n": len(sep), "pnl": sum(r["pnl"] for r in sep)},
        "non_sep_meetings": {"n": len(nonsep), "pnl": sum(r["pnl"] for r in nonsep)},
    }


def _run(config_path: str, hourly: bool):
    r = yaml.safe_load(Path(config_path).read_text())
    cfg = _build_cfg(r, None, None)
    bt = Backtester()
    if hourly:
        bars = load_bar_lists(HOURLY_SNAPSHOT)
        bt._capture_snapshot = lambda *a, **k: None  # type: ignore[assignment]
        bt._fetch_bars_multi = lambda *a, **k: {"1h": bars}  # type: ignore[assignment]
        bt._load_benchmark = lambda *a, **k: None  # type: ignore[assignment]
    strat = build_strategy(r["strategy"], r.get("strategy_params") or {}, r.get("interval", "1d"))
    res = bt.run_portfolio(strat, cfg)
    return res, _round_trips(res.trades)


def _timestamp_proof(rts: list[dict]) -> list[dict]:
    """Hourly existential check: exit fill strictly before 14:00 ET on
    the announcement day; entry the prior session's late afternoon."""
    rows = []
    for r in rts:
        exit_et = r["exit_ts_et"]
        exit_time = exit_et[11:16]
        rows.append({
            "announcement": r["announcement"],
            "entry_et": r["entry_ts_et"], "exit_et": exit_et,
            "exit_on_announcement_day": exit_et[:10] == r["announcement"],
            "exit_before_1400_et": exit_time < "14:00",
        })
    return rows


def main() -> int:
    report: dict = {}
    for name, cfg_path, hourly in (
        ("daily_fomc_drift_spy", "configs/fomc_t2_spy_k2.yaml", False),
        ("hourly_fomc_drift_spy", "configs/fomc_t2h_spy.yaml", True),
    ):
        res, rts = _run(cfg_path, hourly)
        m = res.metrics
        entry = {
            "config": cfg_path,
            "metrics": {"total_return": m.total_return, "sharpe": m.sharpe_ratio,
                        "pf": m.profit_factor, "max_dd": m.max_drawdown},
            "analytics": _analytics(rts),
            "round_trips": rts,
        }
        if hourly:
            proof = _timestamp_proof(rts)
            entry["timestamp_proof"] = proof
            bad = [p for p in proof if not (p["exit_before_1400_et"] and p["exit_on_announcement_day"])]
            entry["timestamp_proof_all_pass"] = not bad
            print(f"[{name}] timestamp proof: {len(proof) - len(bad)}/{len(proof)} exits "
                  f"before 14:00 ET on announcement day"
                  + (f"  VIOLATIONS: {bad}" if bad else "  ALL PASS"), flush=True)
        a = entry["analytics"]
        print(f"[{name}] n={a['n_trades']} total=${a['total_pnl']:,.0f} "
              f"win_rate={a['win_rate']:.2f} top5_share={a['top5_pnl_share_of_total']:.2f} "
              f"ex_best_year=${a['ex_best_year']:,.0f} ex_best_trade=${a['ex_best_trade']:,.0f}",
              flush=True)
        print(f"  by year: {a['pnl_by_year']}", flush=True)
        print(f"  SEP: n={a['sep_meetings']['n']} ${a['sep_meetings']['pnl']:,.0f} | "
              f"non-SEP: n={a['non_sep_meetings']['n']} ${a['non_sep_meetings']['pnl']:,.0f}",
              flush=True)
        report[name] = entry

    out = Path("data/mcpt/fomc_trade_audit.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
