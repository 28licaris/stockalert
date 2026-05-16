#!/usr/bin/env python3
"""
Seed ClickHouse OHLCV for the curated universe (or explicit symbols) using
Schwab price history via ``BackfillService``.

**This script never writes S3.** For Schwab → stock-lake Parquet, use
``scripts/schwab_lake_backfill.py`` instead.

This mirrors ``polygon_flatfiles_bulk_backfill.py`` ergonomics (``--symbols
seed``, dotenv from ``scripts/.env``) but writes **only ClickHouse** through
the REST history provider — not Polygon flat files.

Requires ``HISTORY_PROVIDER`` / ``DATA_PROVIDER`` such that
``effective_history_provider`` is ``schwab`` (override with
``--allow-non-schwab-history`` if you know what you are doing).

Default lookbacks are **48 days** for quick, deep (1m), and intraday (5m),
which matches Schwab's practical ~48-day ceiling for 1-minute bars. Daily
history stays long (default 730d) in one provider call. Override per kind
with ``--quick-days`` / ``--deep-days`` / ``--intraday-days``, or set all
three intraday windows at once with ``--window-days N``.

S3 stock-lake (Parquet under ``STOCK_LAKE_BUCKET``) is **not** written here;
only ClickHouse is populated. Use ``schwab_lake_backfill.py`` for S3. A
follow-on nightly job can export streamed Schwab bars from ClickHouse to
the lake layout — that CH→S3 pipeline is not implemented yet.

Examples
--------
Seed the default 100-ticker universe (48d quick/deep/5m + long daily)::

    poetry run python scripts/schwab_seed_backfill.py --symbols seed

Same but force a single window for the three intraday-ish kinds::

    poetry run python scripts/schwab_seed_backfill.py --symbols seed --window-days 45

Dry-run (no ClickHouse / no API calls)::

    poetry run python scripts/schwab_seed_backfill.py --symbols seed --dry-run

Explicit tickers plus extra symbols from a file (one symbol per line)::

    poetry run python scripts/schwab_seed_backfill.py \\
        --symbols AAPL,MSFT --symbols-file ./extra.txt
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env", override=False)
load_dotenv(override=False)

from app.config import settings  # noqa: E402
from app.data.seed_universe import SEED_SYMBOLS  # noqa: E402
from app.db import init_schema  # noqa: E402
from app.services.backfill_service import backfill_service  # noqa: E402

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"done", "skipped", "error", "throttled"})


def _resolve_schwab_symbols(spec: str) -> list[str]:
    s = (spec or "").strip().lower()
    if s in ("all", "*", ""):
        raise ValueError(
            "'all' / empty filter is not supported for Schwab seed; "
            "use 'seed' or an explicit comma-separated list.",
        )
    if s in ("seed", "seed-100", "seed_100"):
        return list(SEED_SYMBOLS)
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


def _symbols_from_file(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line.upper())
    return out


def _merge_unique(primary: list[str], extra: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for s in primary + extra:
        u = s.upper().strip()
        if u and u not in seen:
            seen.add(u)
            merged.append(u)
    return merged


async def _wait_kind(sym: str, kind: str, *, timeout_s: float) -> dict:
    sym_u = sym.upper()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = backfill_service.status(sym_u)
        row = st.get(sym_u)
        if not row:
            await asyncio.sleep(0.25)
            continue
        job = row.get(kind) or {}
        state = job.get("state") or "idle"
        if state in _TERMINAL:
            return dict(job, kind=kind, symbol=sym_u)
        await asyncio.sleep(0.5)
    return {
        "symbol": sym_u,
        "kind": kind,
        "state": "error",
        "error": f"timeout after {timeout_s:.0f}s",
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Schwab-backed OHLCV seed into ClickHouse (seed universe or list).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--symbols",
        default="seed",
        help="'seed' (default) or comma-separated tickers (not 'all').",
    )
    p.add_argument(
        "--symbols-file",
        type=Path,
        default=None,
        help="Optional path: one ticker per line (merged with --symbols).",
    )
    p.add_argument(
        "--window-days",
        type=int,
        default=None,
        metavar="N",
        help="If set, use N days for quick, deep, and intraday (overrides those three).",
    )
    p.add_argument(
        "--quick-days",
        type=int,
        default=48,
        help="Quick 1m lookback (default 48; Schwab-friendly).",
    )
    p.add_argument(
        "--deep-days",
        type=int,
        default=48,
        help="Deep 1m gap-fill lookback (default 48; ~Schwab 1m API limit).",
    )
    p.add_argument(
        "--intraday-days",
        type=int,
        default=48,
        help="5m intraday lookback (default 48; raise e.g. 270 if you need more 5m).",
    )
    p.add_argument(
        "--daily-days",
        type=int,
        default=730,
        help="Native daily candles lookback (default 730).",
    )
    p.add_argument(
        "--timeout-seconds",
        type=float,
        default=7200.0,
        help="Max wait per job kind per symbol (default 2h).",
    )
    p.add_argument(
        "--allow-non-schwab-history",
        action="store_true",
        help="Run even when effective history provider is not schwab.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print symbols and windows only; do not touch ClickHouse.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


async def _async_main(args: argparse.Namespace) -> int:
    if not args.allow_non_schwab_history:
        if settings.effective_history_provider != "schwab":
            print(
                f"FAIL: effective history provider is "
                f"{settings.effective_history_provider!r} (expected 'schwab'). "
                f"Set DATA_PROVIDER/HISTORY_PROVIDER or pass "
                f"--allow-non-schwab-history.",
                file=sys.stderr,
            )
            return 2

    try:
        base = _resolve_schwab_symbols(args.symbols)
    except ValueError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2

    extra: list[str] = []
    if args.symbols_file is not None:
        if not args.symbols_file.is_file():
            print(f"FAIL: symbols file not found: {args.symbols_file}", file=sys.stderr)
            return 2
        extra = _symbols_from_file(args.symbols_file)

    symbols = _merge_unique(base, extra)
    if not symbols:
        print("FAIL: no symbols to process", file=sys.stderr)
        return 2

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    if args.window_days is not None:
        quick_d = deep_d = intra_d = max(1, int(args.window_days))
    else:
        quick_d, deep_d, intra_d = args.quick_days, args.deep_days, args.intraday_days

    print(
        f"Schwab seed: {len(symbols)} symbol(s); "
        f"quick={quick_d}d deep={deep_d}d intraday={intra_d}d daily={args.daily_days}d",
    )
    if args.dry_run:
        print("dry-run: first 20 symbols:", ", ".join(symbols[:20]))
        if len(symbols) > 20:
            print(f"  ... and {len(symbols) - 20} more")
        return 0

    await asyncio.to_thread(init_schema)
    await backfill_service.start()
    exit_code = 0
    try:
        pipeline = (
            ("quick", quick_d, backfill_service.enqueue_quick),
            ("deep", deep_d, backfill_service.enqueue_deep),
            ("intraday", intra_d, backfill_service.enqueue_intraday),
            ("daily", args.daily_days, backfill_service.enqueue_daily),
        )
        for sym in symbols:
            for kind, days, enqueue in pipeline:
                enqueue(sym, days=days, force=True)
                result = await _wait_kind(sym, kind, timeout_s=args.timeout_seconds)
                state = result.get("state")
                print(f"  {sym} {kind}: {state}")
                if state == "error":
                    err = result.get("error") or result.get("reason") or "unknown"
                    print(f"    error: {err}", file=sys.stderr)
                    exit_code = 1
                elif state == "throttled":
                    print(
                        f"    warning: throttled — {result.get('reason', '')}",
                        file=sys.stderr,
                    )
                    exit_code = 1
    finally:
        await backfill_service.stop()

    return exit_code


def main() -> None:
    args = _build_parser().parse_args()
    try:
        code = asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
