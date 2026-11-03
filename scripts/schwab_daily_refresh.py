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

`ohlcv_daily` is ReplacingMergeTree(version) ORDER BY (symbol, timestamp), so
re-running is idempotent — a repeated (symbol, day) collapses to the highest
version rather than duplicating.

    poetry run python scripts/schwab_daily_refresh.py --dry-run
    poetry run python scripts/schwab_daily_refresh.py
    poetry run python scripts/schwab_daily_refresh.py --symbols SPY,QQQ --start 2026-08-13
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.providers.schwab_provider import SchwabProvider  # noqa: E402

ET = ZoneInfo("America/New_York")
TABLE = "ohlcv_daily"
# ohlcv_daily stores the ET trading day stamped at 14:30 UTC — same convention
# as polygon_grouped_daily, so downstream date bucketing stays identical.
DAY_HOUR_UTC, DAY_MIN_UTC = 14, 30
COLS = ["symbol", "timestamp", "open", "high", "low", "close", "volume", "version"]


def _universe(client) -> list[str]:
    """Active symbols from ClickHouse — the universe lives in the DB, not a file."""
    rows = client.query(
        "SELECT symbol FROM stream_universe WHERE is_active = 1 ORDER BY symbol"
    ).result_rows
    return [r[0] for r in rows]


def _provider() -> SchwabProvider:
    token = settings.get_schwab_refresh_token()
    if not token:
        raise SystemExit(
            "No Schwab refresh token. Run scripts/schwab_get_refresh_token.py, and "
            "note SCHWAB_REFRESH_TOKEN in .env overrides SCHWAB_REFRESH_TOKEN_FILE."
        )
    return SchwabProvider(
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        refresh_token=token,
        callback_url=settings.schwab_callback_url or None,
        base_url=settings.schwab_base_url,
        refresh_token_file=settings.schwab_refresh_token_file or None,
    )


def _day_ts(d: date) -> datetime:
    return datetime.combine(d, time(DAY_HOUR_UTC, DAY_MIN_UTC), tzinfo=timezone.utc)


async def _fetch(p: SchwabProvider, sym: str, start: date, end: date):
    """Daily bars for one symbol. Returns (rows, status)."""
    df = await p.historical_df(
        sym,
        datetime.combine(start, time(0, 0), tzinfo=timezone.utc),
        datetime.combine(end + timedelta(days=1), time(0, 0), tzinfo=timezone.utc),
        timeframe="1d",
    )
    if df is None or len(df) == 0:
        # Schwab answered 200 with empty candles. Distinct from an error, and
        # distinct from "the window had no trading days" — the caller decides.
        return [], "empty"

    out = []
    for ts, r in df.iterrows():
        d = ts.astimezone(ET).date() if getattr(ts, "tzinfo", None) else ts.date()
        if not (start <= d <= end):
            continue
        out.append([sym, _day_ts(d), float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]), float(r.get("volume") or 0)])
    return out, ("ok" if out else "out_of_range")


async def run(symbols, start: date, end: date, dry_run: bool, sleep_s: float) -> int:
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

    before = client.query(f"SELECT count(), max(timestamp) FROM {TABLE} FINAL").result_rows[0]
    client.insert(TABLE, batch, column_names=COLS)
    after = client.query(f"SELECT count(), max(timestamp) FROM {TABLE} FINAL").result_rows[0]
    print(f"\n  inserted {len(batch):,} rows")
    print(f"  {TABLE}: {before[0]:,} → {after[0]:,} rows · newest {before[1]} → {after[1]}")

    # A write that did not move the read is a failure, however cleanly it returned.
    if after[1] <= before[1] and after[0] <= before[0]:
        print("  FAILED — table did not advance after insert")
        return 1
    return 0 if not buckets["error"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list; default = active stream_universe")
    ap.add_argument("--start", default="", help="ET date YYYY-MM-DD; default = day after CH max")
    ap.add_argument("--end", default="", help="ET date YYYY-MM-DD; default = yesterday ET")
    ap.add_argument("--sleep-seconds", type=float, default=0.05)
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

    return asyncio.run(run(symbols, start, end, a.dry_run, a.sleep_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
