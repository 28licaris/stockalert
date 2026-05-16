"""
One-shot smoke for BackfillService.enqueue_deep flat-files dispatch.

NOT a unit test; runs against live ClickHouse + Polygon Flat Files using
``scripts/.env``. Asserts that when ``HISTORY_PROVIDER=polygon`` and
``POLYGON_FLATFILES_ENABLED=true``, ``enqueue_deep`` actually routes
through the flat-files path (reason string says so) instead of the
REST chunker.

Usage:

    poetry run python scripts/_smoke_deep_dispatch.py [SYMBOL] [DAYS]

Defaults to TLT for 5 days. Keep the symbol + window small.
"""
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.config import settings  # noqa: E402
from app.services.ingest.backfill_service import backfill_service  # noqa: E402


async def main(symbol: str, days: int) -> int:
    print(f"history_provider           : {settings.effective_history_provider}")
    print(f"polygon_flatfiles_enabled  : {settings.polygon_flatfiles_enabled}")
    print(f"polygon_s3_access_key_id   : "
          f"{(settings.polygon_s3_access_key_id or '')[:8]}...")
    print()

    enq = backfill_service.enqueue_deep(symbol, days=days, force=True)
    print(f"enqueue_deep({symbol}, days={days}) -> {enq}")

    # Drain the task.
    tasks = list(backfill_service._tasks.values())
    if not tasks:
        print("WARN: no task created (already running or throttled)")
        return 1
    for t in tasks:
        try:
            await t
        except Exception as e:
            print(f"task raised: {e}")

    st = backfill_service.status(symbol)[symbol.upper()]["deep"]
    print()
    print(f"final deep status: state={st['state']}  bars={st['bars']}  "
          f"chunks_done={st['chunks_done']}/{st['chunks_total']}")
    reason = st.get("reason") or ""
    print(f"reason: {reason}")

    if "flat-files" in reason:
        print()
        print("PASS: deep dispatch routed through Polygon Flat Files")
        return 0
    print()
    print("FAIL: deep dispatch did NOT use flat-files. Reason missing "
          "the 'flat-files:' prefix; check that HISTORY_PROVIDER=polygon "
          "and POLYGON_FLATFILES_ENABLED=true in .env.")
    return 2


if __name__ == "__main__":
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "TLT"
    d = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    rc = asyncio.run(main(sym, d))
    sys.exit(rc)
