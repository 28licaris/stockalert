#!/usr/bin/env python3
"""
Futures history backfill — pull 1-minute bars from Schwab REST
pricehistory for CONTINUOUS futures roots (/ES, /MES, …) and land them in
``futures.schwab_futures`` (Iceberg). One Iceberg append per CME session
day, rows from N roots merged into a single canonical frame.

Mirror of ``scripts/schwab_history_backfill.py`` (equities) — it reuses
that script's canonical-frame + ET-day-window helpers — but targets the
futures lake table and the CME Globex session calendar (Sun-Fri; only
Saturday is dark).

Schwab's pricehistory has a practical ~48-day lookback for 1-minute bars,
so this is typically run with ``--days 48``.

Idempotency: identical to the equities backfill — Iceberg ``append`` never
fails on duplicates, but re-running the same (day, root) set DOES create
duplicate rows in ``futures.schwab_futures``. The nightly path
(``app.services.ingest.nightly_futures_refresh``) enforces idempotency
upstream via the gap pre-scan; for clean manual re-runs, delete the
affected partitions first (recipe in docs/architecture_v2/07_runbook.md).

Run:
    poetry run python scripts/futures_history_backfill.py --symbols seed --days 48

    # narrower scope:
    poetry run python scripts/futures_history_backfill.py --symbols /ES,/NQ --days 7
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.config import settings  # noqa: E402
from app.services.futures.gaps import is_futures_session_day  # noqa: E402
from app.services.futures.schemas import FUTURES_SEED_ROOTS  # noqa: E402
from app.services.futures.sink import futures_iceberg_sink  # noqa: E402
from scripts.schwab_history_backfill import (  # noqa: E402
    _iter_days,
    _schwab_1m_to_canonical,
    _us_eastern_day_window,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("futures-history-backfill")


# ─────────────────────────────────────────────────────────────────────
# CLI helpers
# ─────────────────────────────────────────────────────────────────────

def _resolve_symbols(spec: str) -> list[str]:
    """'seed' → FUTURES_SEED_ROOTS; CSV → explicit roots (leading '/'
    enforced). 'all' is NOT supported — Schwab REST is per-symbol."""
    s = (spec or "").strip().lower()
    if s in ("seed", "seed-roots", "seed_roots", ""):
        return list(FUTURES_SEED_ROOTS)
    if s in ("all", "*"):
        raise ValueError("Use 'seed' or explicit roots; 'all' is not supported for futures REST.")
    out: list[str] = []
    for tok in spec.split(","):
        t = tok.strip().upper()
        if t:
            out.append(t if t.startswith("/") else "/" + t)
    return out


# ─────────────────────────────────────────────────────────────────────
# Main backfill
# ─────────────────────────────────────────────────────────────────────

async def run_backfill(
    *,
    symbols: list[str],
    start: date,
    end: date,
    sleep_seconds: float,
    dry_run: bool,
) -> int:
    if not (settings.stock_lake_bucket or "").strip():
        print("FAIL: STOCK_LAKE_BUCKET is empty.", file=sys.stderr)
        return 2

    from app.config import get_provider
    provider = get_provider("schwab")

    sink = futures_iceberg_sink() if not dry_run else None

    days = _iter_days(start, end)
    logger.info(
        "Schwab → futures.schwab_futures: %d root(s) × %d day(s) "
        "(%s .. %s)  sleep=%.2fs",
        len(symbols), len(days), start, end, sleep_seconds,
    )

    ok_days = 0
    skipped_days = 0
    err_days = 0
    total_rows = 0

    for d in days:
        if not is_futures_session_day(d):
            logger.info("  %s: skip (Saturday — CME Globex dark)", d)
            skipped_days += 1
            continue

        t0, t1 = _us_eastern_day_window(d)
        per_symbol_frames: list[pd.DataFrame] = []
        for sym in symbols:
            try:
                raw = await provider.historical_df(sym, t0, t1, timeframe="1Min")
                canonical = _schwab_1m_to_canonical(raw, sym)
                if not canonical.empty:
                    per_symbol_frames.append(canonical)
            except Exception as e:
                logger.error("  %s %s: API error: %s", d, sym, e)
            await asyncio.sleep(max(0.0, sleep_seconds))

        merged = pd.concat(per_symbol_frames, ignore_index=True) if per_symbol_frames else pd.DataFrame()

        if dry_run:
            logger.info(
                "  %s: dry-run rows=%-7d roots_with_data=%d/%d",
                d, len(merged), len(per_symbol_frames), len(symbols),
            )
            continue

        if merged.empty:
            logger.info("  %s: no rows from any root", d)
            skipped_days += 1
            continue

        result = await sink.write(merged, file_date=d, kind="minute", provider="schwab")
        if result.status == "ok":
            ok_days += 1
            total_rows += result.bars_written
            logger.info(
                "  %s: ok rows=%-7d snapshot=%s",
                d, result.bars_written, result.metadata.get("snapshot_id_after"),
            )
        elif result.status == "skipped":
            skipped_days += 1
            logger.info("  %s: skipped reason=%s", d, result.metadata.get("reason"))
        else:
            err_days += 1
            logger.error("  %s: %s err=%s", d, result.status, result.error)

    logger.info(
        "Done: ok=%d skipped=%d errors=%d total_rows=%s",
        ok_days, skipped_days, err_days, f"{total_rows:,}",
    )
    return 1 if err_days else 0


# ─────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    s = s.strip().lower()
    if s == "yesterday":
        return datetime.now(timezone.utc).date() - timedelta(days=1)
    if s == "today":
        return datetime.now(timezone.utc).date()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="seed",
                   help="'seed' for FUTURES_SEED_ROOTS, or comma-separated roots (/ES,/NQ).")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--days", type=int, default=None,
                     help="Backfill the last N session days ending at --end (or yesterday).")
    grp.add_argument("--start", type=_parse_date, default=None,
                     help="ET calendar start date (YYYY-MM-DD).")
    p.add_argument("--end", type=_parse_date, default=None,
                   help="ET calendar end date (default: yesterday in UTC).")
    p.add_argument("--sleep-seconds", type=float, default=0.05,
                   help="Sleep between per-root API calls (rate-limit cushion).")
    p.add_argument("--dry-run", action="store_true",
                   help="Pull from Schwab + canonicalize, but don't write to Iceberg.")
    return p


def main() -> None:
    args = _build_parser().parse_args()

    end = args.end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    if args.days is not None:
        start = end - timedelta(days=args.days - 1)
    elif args.start is not None:
        start = args.start
    else:
        start = end - timedelta(days=47)  # Schwab's 48-day practical max

    try:
        symbols = _resolve_symbols(args.symbols)
    except ValueError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    if not symbols:
        print("FAIL: no symbols resolved.", file=sys.stderr)
        sys.exit(2)

    code = asyncio.run(run_backfill(
        symbols=symbols,
        start=start,
        end=end,
        sleep_seconds=args.sleep_seconds,
        dry_run=args.dry_run,
    ))
    sys.exit(code)


if __name__ == "__main__":
    main()
