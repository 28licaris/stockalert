"""Warm the instrument_names company-name cache.

By default warms the live streaming universe (the symbols shown on the Stream
and Watchlist pages) so lookups are instant ClickHouse hits. Pass extra symbols
to warm them too.

  poetry run python scripts/backfill_instrument_names.py
  poetry run python scripts/backfill_instrument_names.py PLTR COIN HOOD
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.instruments.names import warm, warm_stream_universe  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    extra = [a.strip().upper() for a in argv if a.strip()]
    print("warming streaming universe…", flush=True)
    n = warm_stream_universe()
    print(f"  {n} universe symbols have names")
    if extra:
        m = warm(extra)
        print(f"  {m}/{len(extra)} extra symbols have names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
