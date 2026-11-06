#!/usr/bin/env python3
"""
Fill CH `ohlcv_daily` from **Schwab** REST pricehistory.

Why this exists: `refresh_ohlcv_daily.py` offers only `--source rest`
(Polygon grouped-daily) and `--source lake` (Polygon flat-files). Both are
Polygon, and the Polygon subscription lapsed, so `ohlcv_daily` froze at
2026-08-13 while the Schwab live stream kept `ohlcv_1m` current. Schwab is now
the only live source, so daily bars need a Schwab path.

Two properties of the Schwab REST path worth knowing, both measured:

  * **It is NOT subject to the streaming symbol cap.** Schwab's websocket
    delivers live ticks for only ~293 of 424 subscribed symbols, silently.
    REST pricehistory is request/response per symbol and returns data for
    names that never stream (verified: SPY, TSLA, XLF, XLE all return full
    history via REST while absent from the stream).
  * **Schwab reports unavailable symbols explicitly.** The response carries
    `{"symbol": X, "empty": true, "candles": []}` — a 200, not an error. Some
    universe entries return this for ALL history (BK, MMC, K, and the renamed
    SQ -> XYZ, DFS -> COF). That is distinct from "no bars in this window" and
    is reported separately rather than collapsed into a zero count.

Schwab bars are **pre-adjusted**, so unlike the Polygon path there is no split
adjustment step here. (Consequence worth remembering: a future split will not
retroactively re-adjust rows already written, the way the Polygon path does by
re-dividing history. See the caveat in the handoff doc.)

Writes the **lake first, ClickHouse second** — `equities.schwab_daily_raw` is
ground truth and CH is the serving cache, so CH must never hold a bar the lake
does not. Daily Schwab bars get their own lake table rather than joining
`schwab_universe`, which accepts `(schwab, minute)` only; the lake separates
granularity by table (cf. polygon_raw vs polygon_daily_raw) because there is no
`kind` column to disambiguate them.

`ohlcv_daily` is ReplacingMergeTree(version) ORDER BY (symbol, timestamp), so
re-running is idempotent — a repeated (symbol, day) collapses to the highest
version rather than duplicating. Lake appends are not deduped by Iceberg, so a
re-run of the same window double-appends there; use --skip-lake when
re-running purely to repair CH.

    poetry run python scripts/schwab_daily_refresh.py --dry-run
    poetry run python scripts/schwab_daily_refresh.py
    poetry run python scripts/schwab_daily_refresh.py --symbols SPY,QQQ --start 2026-08-13
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingest.schwab_daily import (  # noqa: E402
    COLS, ET, TABLE, build_schwab_provider, fetch_daily_bars as _fetch, write_lake,
)


def _universe(client) -> list[str]:
    """Active symbols from ClickHouse — the universe lives in the DB, not a file."""
    rows = client.query(
        "SELECT symbol FROM stream_universe WHERE is_active = 1 ORDER BY symbol"
    ).result_rows
    return [r[0] for r in rows]


def _provider():
    try:
        return build_schwab_provider()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


async def run(symbols, start: date, end: date, dry_run: bool, sleep_s: float,
              *, skip_lake: bool = False) -> int:
    from app.db.client import get_client

    client = get_client()
    p = _provider()
    version = int(datetime.now(timezone.utc).timestamp() * 1000)

    buckets: dict[str, list[str]] = defaultdict(list)
    total_rows = 0
    batch: list[list] = []

    print(f"Schwab daily refresh · {start} → {end} · {len(symbols)} symbols"
          f"{' · DRY RUN' if dry_run else ''}\n")

    for i, sym in enumerate(symbols, 1):
        try:
            rows, status = await _fetch(p, sym, start, end)
        except Exception as exc:                       # surfaced, never swallowed
            buckets["error"].append(sym)
            print(f"  [{i:>4}/{len(symbols)}] {sym:<6} ERROR {str(exc)[:90]}")
            continue

        buckets[status].append(sym)
        total_rows += len(rows)
        batch.extend([r + [version] for r in rows])
        if i % 50 == 0 or i == len(symbols):
            print(f"  [{i:>4}/{len(symbols)}] {total_rows:,} rows so far", flush=True)
        if sleep_s:
            await asyncio.sleep(sleep_s)

    print(f"\n  ok={len(buckets['ok'])}  empty={len(buckets['empty'])}  "
          f"out_of_range={len(buckets['out_of_range'])}  error={len(buckets['error'])}")

    if buckets["empty"]:
        # These are the ones Schwab explicitly has nothing for, at any date.
        # Universe hygiene, not a transient failure — name them so they can be
        # fixed (renamed, delisted, or not entitled) instead of silently missing.
        s = buckets["empty"]
        print(f"  Schwab reports EMPTY for {len(s)} symbol(s): {', '.join(sorted(s)[:25])}"
              + (" …" if len(s) > 25 else ""))
    if buckets["error"]:
        print(f"  ERRORS: {', '.join(sorted(buckets['error'])[:25])}")

    if dry_run:
        print(f"\n  DRY RUN — would insert {total_rows:,} rows")
        return 0
    if not batch:
        print("\n  nothing to insert")
        return 1

    if not skip_lake:
        rc = await _write_lake(batch, version)
        if rc != 0:
            # Lake is ground truth. If it did not take, do NOT load CH — a CH
            # row with no lake row behind it is exactly the drift this ordering
            # exists to prevent.
            print("  lake write failed — refusing to load ClickHouse")
            return rc

    before = client.query(f"SELECT count(), max(timestamp) FROM {TABLE} FINAL").result_rows[0]
    client.insert(TABLE, batch, column_names=COLS)
    after = client.query(f"SELECT count(), max(timestamp) FROM {TABLE} FINAL").result_rows[0]
    print(f"\n  inserted {len(batch):,} rows")
    print(f"  {TABLE}: {before[0]:,} → {after[0]:,} rows · newest {before[1]} → {after[1]}")

    # Verify the read side actually reflects the write. Row COUNT is the wrong
    # test: ohlcv_daily is ReplacingMergeTree, so an idempotent re-run of an
    # already-loaded window legitimately leaves the count unchanged. What must
    # hold is that every (symbol, day) we just sent is now present.
    want_max = max(r[1] for r in batch)
    got = client.query(
        f"SELECT count() FROM {TABLE} FINAL WHERE timestamp = {{ts:DateTime64(3)}}",
        parameters={"ts": want_max},
    ).result_rows[0][0]
    if got == 0:
        print(f"  FAILED — nothing readable at {want_max} after insert")
        return 1
    if after[0] < before[0]:
        print(f"  FAILED — row count went DOWN ({before[0]:,} -> {after[0]:,})")
        return 1
    print(f"  verified: {got:,} row(s) readable at {want_max}")
    return 0 if not buckets["error"] else 1



async def _write_lake(batch: list[list], version: int) -> int:
    """Append the fetched bars to `equities.schwab_daily_raw`, one day at a time.

    One append per trading day matches the partitioning (month) and the existing
    nightly writers, and keeps a failure scoped to a single day rather than the
    whole run. The logic lives in ``app.services.ingest.schwab_daily.write_lake``.
    """
    res = await write_lake(batch, version, per_day=True)
    for m in res.messages:
        print(f"  {m}")
    print(f"  lake: {res.written:,} rows appended to equities.schwab_daily_raw"
          + (f" · {res.failed_units} day(s) FAILED" if res.failed_units else ""))
    return 1 if res.failed_units else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list; default = active stream_universe")
    ap.add_argument("--start", default="", help="ET date YYYY-MM-DD; default = day after CH max")
    ap.add_argument("--end", default="", help="ET date YYYY-MM-DD; default = yesterday ET")
    ap.add_argument("--sleep-seconds", type=float, default=0.05)
    ap.add_argument("--skip-lake", action="store_true",
                    help="CH only. Lake appends are not deduped, so use this when "
                         "re-running a window purely to repair ClickHouse.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    from app.db.client import get_client

    client = get_client()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or _universe(client)
    if not symbols:
        print("no symbols — stream_universe is empty and none were passed")
        return 1

    if a.start:
        start = date.fromisoformat(a.start)
    else:
        mx = client.query(f"SELECT max(timestamp) FROM {TABLE} FINAL").result_rows[0][0]
        start = (mx.date() + timedelta(days=1)) if mx else date(2026, 1, 1)
    end = date.fromisoformat(a.end) if a.end else (datetime.now(ET).date() - timedelta(days=1))

    if end < start:
        print(f"nothing to do — CH is already current through {start - timedelta(days=1)}")
        return 0

    return asyncio.run(run(symbols, start, end, a.dry_run, a.sleep_seconds,
                       skip_lake=a.skip_lake))


if __name__ == "__main__":
    raise SystemExit(main())
