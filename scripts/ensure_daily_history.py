#!/usr/bin/env python3
"""Make sure symbols have enough daily history; fetch and store what is missing.

A thin CLI over ``app.services.ingest.schwab_daily.ensure_daily_history``: Schwab REST
daily bars -> lake (``equities.schwab_daily_raw``) first, then ClickHouse
(``ohlcv_daily``). Only the span *before* a symbol's earliest stored day is fetched
(lake appends are not deduplicated), so it is safe to re-run.

  python scripts/ensure_daily_history.py --symbols SCCO,ERO,HBM
  python scripts/ensure_daily_history.py --symbols SCCO --min-sessions 250 --years 3 --dry-run

Exit status: 0 when every symbol ended ``ok`` or ``already_covered``; 1 otherwise
(an unknown ticker, a lake failure, ... — each is named in the table).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingest.schwab_daily import (  # noqa: E402
    DEFAULT_MIN_SESSIONS,
    DEFAULT_YEARS,
    ClickHouseDailyStore,
    ensure_daily_history,
)


def main(argv: list[str] | None = None, *, ensure=ensure_daily_history, store=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", required=True, help="comma-separated tickers")
    ap.add_argument("--min-sessions", type=int, default=DEFAULT_MIN_SESSIONS)
    ap.add_argument("--years", type=int, default=DEFAULT_YEARS, help="how far back a first fetch reaches")
    ap.add_argument("--dry-run", action="store_true", help="show what is stored; fetch and write nothing")
    a = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if not symbols:
        print("no symbols given")
        return 1

    if a.dry_run:
        cov = (store or ClickHouseDailyStore()).coverage(symbols)
        print(f"{'symbol':<8} {'sessions':>8}  {'first':<10}  {'last':<10}  verdict")
        for s in symbols:
            c = cov.get(s)
            n = c.sessions if c else 0
            verdict = "covered" if n >= a.min_sessions else "WOULD FETCH"
            print(f"{s:<8} {n:>8}  {str(c.first) if c else '-':<10}  {str(c.last) if c else '-':<10}  {verdict}")
        return 0

    results = asyncio.run(ensure(symbols, min_sessions=a.min_sessions, years=a.years))
    print(f"{'symbol':<8} {'status':<16} {'rows':>7}  detail")
    for r in results:
        print(f"{r.symbol:<8} {r.status:<16} {r.rows:>7}  {r.detail}")
    bad = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} ok" + (f"; NOT ok: {', '.join(r.symbol for r in bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
