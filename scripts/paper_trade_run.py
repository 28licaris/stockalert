"""
Forward paper-trading runner (M3). Run daily (cron / scheduled trigger):

  poetry run python scripts/paper_trade_run.py --config configs/paper_momentum.yaml

Loads the LOCKED config, re-runs it through the same engine against the latest CH
data, persists state, and prints the forward (post-go-live) track record. The
go_live boundary is fixed in the config — only the slice after it is the real record.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app.services.sim.paper.schemas import PaperRunConfig  # noqa: E402
from app.services.sim.paper.service import append_alerts, build_status, run_paper  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Paper run config YAML.")
    a = ap.parse_args(argv)
    cfg = PaperRunConfig(**yaml.safe_load(Path(a.config).read_text()))
    state = run_paper(cfg)
    s = build_status(state)
    append_alerts(s)
    # ── ALERTS: today's entry/exit signals (what a subscriber would be pinged on) ──
    if s.today_entries or s.today_exits:
        print(f"\n🔔 SIGNAL ALERTS for {str(s.computed_through)[:10]}")
        for p in s.today_entries:
            side = "LONG" if p.quantity >= 0 else "SHORT"
            print(f"   ENTRY  {side:5} {p.symbol:6} @ ${p.avg_entry_price:.2f}")
        for t in s.today_exits:
            print(f"   EXIT         {t.symbol:6} P&L ${t.realized_pnl:+,.0f}")
    else:
        print(f"\n🔕 No new entry/exit signals for {str(s.computed_through)[:10]}.")
    print(f"\nPAPER  {s.name}   live since {str(s.go_live)[:10]}  (computed through {str(s.computed_through)[:10]})")
    print("  " + "-" * 56)
    print(f"  days live          {s.days_live}")
    print(f"  equity @ go-live   ${s.equity_at_go_live:,.0f}")
    print(f"  current equity     ${s.current_equity:,.0f}")
    print(f"  FORWARD return     {s.forward_return * 100:+.2f}%")
    print(f"  forward trades     {s.forward_n_trades}"
          + (f"  (win {s.forward_win_rate * 100:.0f}%)" if s.forward_win_rate is not None else ""))
    print(f"  open positions     {s.n_open_positions}")
    for p in s.open_positions[:15]:
        print(f"     {p.symbol:6} {p.quantity:>8.0f} @ ${p.avg_entry_price:.2f}  uPnL ${p.unrealized_pnl:+,.0f}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
