#!/usr/bin/env python3
"""Fetch Schwab option-chain snapshots and write them to the options lake.

Run:
    poetry run python scripts/options_chain_snapshot.py --symbols AAPL,MSFT

Dry-run fetches and parses Schwab data but does not write Iceberg:
    poetry run python scripts/options_chain_snapshot.py --symbols AAPL --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.services.options.schemas import OptionSnapshotIngestResult  # noqa: E402
from app.services.options.service import (  # noqa: E402
    DEFAULT_CHAIN_PARAMS,
    OptionsSnapshotService,
)
from app.services.options.universe import (  # noqa: E402
    parse_symbols,
    resolve_options_symbol_spec,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("options-chain-snapshot")


def build_request_params(args: argparse.Namespace) -> dict:
    params = dict(DEFAULT_CHAIN_PARAMS)
    params["strikeCount"] = args.strike_count
    if args.from_date:
        params["fromDate"] = args.from_date
    if args.to_date:
        params["toDate"] = args.to_date
    if args.contract_type:
        params["contractType"] = args.contract_type
    return params


def result_line(result: OptionSnapshotIngestResult) -> str:
    base = (
        f"{result.symbol}: status={result.status} "
        f"contracts={result.contracts_parsed} expirations={result.expirations_parsed} "
        f"gex_rows={result.gamma_rows} written={result.rows_written}"
    )
    if result.sink_status:
        base += f" sink={result.sink_status}"
    if result.error:
        base += f" error={result.error}"
    return base


async def run_snapshot(
    *,
    symbols: Sequence[str],
    request_params: dict,
    dry_run: bool,
    service: OptionsSnapshotService | None = None,
) -> int:
    service = service or OptionsSnapshotService.from_settings(dry_run=dry_run)
    snapshot_ts = datetime.now(timezone.utc)
    errors = 0

    log.info(
        "options snapshot: symbols=%d dry_run=%s params=%s",
        len(symbols),
        dry_run,
        request_params,
    )
    for symbol in symbols:
        result = await service.ingest_symbol(
            symbol,
            snapshot_ts=snapshot_ts,
            request_params=request_params,
            dry_run=dry_run,
        )
        if result.status == "error":
            errors += 1
            log.error(result_line(result))
        else:
            log.info(result_line(result))
        log.info("symbol_complete=%s status=%s", result.symbol, result.status)

    log.info("options snapshot complete symbols=%d errors=%d", len(symbols), errors)
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Schwab option-chain snapshots")
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated underlyings, 'active', or 'watchlist:<name>'",
    )
    parser.add_argument(
        "--strike-count",
        type=int,
        default=20,
        help="Strikes above/below ATM (default: 20)",
    )
    parser.add_argument("--contract-type", choices=["CALL", "PUT", "ALL"], default="ALL")
    parser.add_argument("--from-date", help="Optional Schwab fromDate yyyy-MM-dd")
    parser.add_argument("--to-date", help="Optional Schwab toDate yyyy-MM-dd")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse, but do not write Iceberg",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        symbols = resolve_options_symbol_spec(args.symbols)
    except ValueError as e:
        parser.error(str(e))
    params = build_request_params(args)
    return asyncio.run(
        run_snapshot(symbols=symbols, request_params=params, dry_run=args.dry_run)
    )


if __name__ == "__main__":
    raise SystemExit(main())
