"""
Daily paper-trade step for EVERY strategy in the library.

Iterates all registered strategy definitions, re-runs each one's LOCKED config
through the same engine against the latest data, updates its forward record, logs
alerts, and prints a summary + today's entry/exit signals. Adding a strategy to the
library automatically enrolls it here — no per-strategy cron edits.

  poetry run python scripts/paper_run_all.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sim.library.service import list_definitions  # noqa: E402
from app.services.sim.paper.schemas import PaperRunConfig  # noqa: E402
from app.services.sim.paper.service import append_alerts, build_status, run_paper  # noqa: E402


def main() -> int:
    defs = list_definitions()
    if not defs:
        print("No strategies in the library.")
        return 0
    print(f"Paper-running {len(defs)} librar{'y' if len(defs) == 1 else 'ies'} strateg"
          f"{'y' if len(defs) == 1 else 'ies'}…\n")
    for d in defs:
        try:
            cfg = PaperRunConfig(**d.config)
        except Exception as exc:  # noqa: BLE001
            print(f"[{d.name}] SKIP — config not a paper run: {exc}")
            continue
        state = run_paper(cfg)
        s = build_status(state)
        append_alerts(s)
        print(f"=== {d.title} ({d.name}) — through {str(s.computed_through)[:10]} ===")
        print(f"  forward {s.forward_return * 100:+.2f}% · {s.days_live}d · "
              f"{s.forward_n_trades} trades · balance ${s.current_balance:,.0f} · {s.n_open_positions} open")
        if s.today_entries or s.today_exits:
            for p in s.today_entries:
                side = "LONG" if p.quantity >= 0 else "SHORT"
                tgt = f" target ${p.target_price:.2f}" if p.target_price else ""
                stp = f" stop ${p.stop_price:.2f}" if p.stop_price else ""
                print(f"  🔔 ENTRY {side} {p.symbol} @ ${p.avg_entry_price:.2f}{stp}{tgt}")
            for t in s.today_exits:
                print(f"  🔔 EXIT {t.symbol} P&L ${t.realized_pnl:+,.0f}")
        else:
            print("  🔕 no new signals today")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
