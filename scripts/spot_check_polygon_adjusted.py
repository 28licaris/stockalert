#!/usr/bin/env python3
"""
Spot-check `equities.polygon_adjusted` for SELF-CONSISTENCY against a
baseline snapshot of known-good values.

This is a regression detector, NOT an external-truth check. Catches:
  - Adjustment math drifting from the canonical formula (e.g. a future
    refactor that subtly changes the cumulative-factor cumulant).
  - Silent row loss in the Spark write (per-symbol parity).
  - Polygon RAW data changing for an old bar (which would shift adj_close).

Does NOT catch: Polygon's raw data being wrong vs the "true" market.
For that, write a separate tool that queries a different vendor live.

**Baselined cases.** Reference (adj_factor, adj_close) values come from
the validated 2026-05-24 polygon_adjusted snapshot (1634110371703478731),
which itself was hand-validated against Yahoo Finance reference data
(see git blame for the post-cutover validation thread). If a future
adjustment run produces materially different values, this script trips
loudly. AAPL is intentionally absent — its splits (2014 7:1, 2020 4:1)
predate our 2021-01-04 history so there's no testable adjustment.

  - NVDA 2021-07-16 (Fri before 4:1 split on 2021-07-19): cumulative
    factor 40 because of 2024 10:1; adj_close $18.16.
  - NVDA 2024-06-06 (Thu before 10:1 split on 2024-06-07): factor 10;
    adj_close $120.94.
  - TSLA 2022-08-24 (Wed before 3:1 split on 2022-08-25): factor 3;
    adj_close $297.15.
  - AMZN 2022-06-03 (Fri before 20:1 split on 2022-06-06): factor 20;
    adj_close $122.35.
  - GOOGL 2022-07-15 (Fri before 20:1 split on 2022-07-18): factor 20;
    adj_close $111.79.

Plus parity check: per-symbol row count in polygon_raw must equal
polygon_adjusted (Spark adjustment is one-row-in-one-row-out).

Usage:
  poetry run python scripts/spot_check_polygon_adjusted.py
  poetry run python scripts/spot_check_polygon_adjusted.py --symbol NVDA  # one case
  poetry run python scripts/spot_check_polygon_adjusted.py --tolerance 0.005  # ±0.5%

Exit code: 0 if all cases pass, 1 if any fail (suitable for cron / CI).
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.services.equities.schemas import equities_table_id  # noqa: E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402

log = logging.getLogger(__name__)


@dataclass
class SplitCheckCase:
    """One row of the reference data — one (symbol, day-before-split) pair."""
    symbol: str
    bar_date: date
    expected_adj_factor: float
    expected_adj_close: float  # Yahoo Finance reference, recorded once
    note: str


# Reference cases. Hand-picked to cover:
#   - Symbols with single splits (TSLA, AMZN, GOOGL — 1 split each in window)
#   - Symbols with cumulative splits (NVDA — 2 splits, factor product = 40)
# Pre-2021-01-04 splits are not testable (no data in our window).
CASES: list[SplitCheckCase] = [
    # Baselined 2026-05-24 against snapshot 1634110371703478731
    # (Step 3 of Phase 1 cutover). adj_close values are from the
    # RTH-close minute bar (15:59 ET).
    SplitCheckCase(
        symbol="NVDA", bar_date=date(2021, 7, 16),
        expected_adj_factor=40.0, expected_adj_close=18.16,
        note="Fri before NVDA 4:1 (2021-07-19); cumulative 4x10=40 because of 2024 split",
    ),
    SplitCheckCase(
        symbol="NVDA", bar_date=date(2024, 6, 6),
        expected_adj_factor=10.0, expected_adj_close=120.94,
        note="Thu before NVDA 10:1 (2024-06-07)",
    ),
    SplitCheckCase(
        symbol="TSLA", bar_date=date(2022, 8, 24),
        expected_adj_factor=3.0, expected_adj_close=297.15,
        note="Wed before TSLA 3:1 (2022-08-25)",
    ),
    SplitCheckCase(
        symbol="AMZN", bar_date=date(2022, 6, 3),
        expected_adj_factor=20.0, expected_adj_close=122.35,
        note="Fri before AMZN 20:1 (2022-06-06)",
    ),
    SplitCheckCase(
        symbol="GOOGL", bar_date=date(2022, 7, 15),
        expected_adj_factor=20.0, expected_adj_close=111.79,
        note="Fri before GOOGL 20:1 (2022-07-18)",
    ),
]

PARITY_SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMZN", "GOOGL"]


def _fetch_rth_close(table, symbol: str, bar_date: date) -> tuple[float, float] | None:
    """Return (close, adj_factor) of the LAST REGULAR-SESSION minute bar on
    `bar_date` for `symbol`.

    Polygon flat-files include extended hours (4 AM - 8 PM ET), but Yahoo
    Finance's daily "close" is the 4:00 PM ET regular-session close. We
    must filter to RTH to match the reference. The last RTH minute bar is
    timestamped 15:59 ET (the 3:59-4:00 PM minute, which CONTAINS the
    closing price). In UTC that's:
      - EDT (Mar-Nov): 19:59 UTC
      - EST (Nov-Mar): 20:59 UTC

    Returns None if no bars exist on the date for this symbol.
    """
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")

    start_ts = datetime.combine(bar_date, datetime.min.time(), tzinfo=timezone.utc)
    next_day = date.fromordinal(bar_date.toordinal() + 1)
    end_ts = datetime.combine(next_day, datetime.min.time(), tzinfo=timezone.utc)
    arrow = table.scan(
        row_filter=(
            f"symbol = '{symbol}' "
            f"AND timestamp >= '{start_ts.isoformat()}' "
            f"AND timestamp < '{end_ts.isoformat()}'"
        )
    ).to_arrow()
    if arrow.num_rows == 0:
        return None
    ts = arrow.column("timestamp").to_pylist()
    closes = arrow.column("close").to_pylist()
    factors = arrow.column("adj_factor").to_pylist()
    # Filter to RTH-close minute: ET hour == 15 AND minute == 59
    # (= the 3:59-4:00 PM minute, which holds the closing print).
    candidates = [
        i for i, t in enumerate(ts)
        if t.astimezone(NY).hour == 15 and t.astimezone(NY).minute == 59
    ]
    if not candidates:
        # Fallback: latest RTH bar (any minute 9:30-16:00 ET).
        candidates = [
            i for i, t in enumerate(ts)
            if (t.astimezone(NY).hour, t.astimezone(NY).minute) >= (9, 30)
            and (t.astimezone(NY).hour, t.astimezone(NY).minute) <= (16, 0)
        ]
        if not candidates:
            return None
    idx = max(candidates, key=lambda i: ts[i])
    return closes[idx], factors[idx]


def _check_split_case(table, case: SplitCheckCase, tolerance: float) -> bool:
    """Return True if case passes."""
    res = _fetch_rth_close(table, case.symbol, case.bar_date)
    if res is None:
        log.error(
            "✗ %s %s: NO BARS FOUND (symbol absent or date pre-history)",
            case.symbol, case.bar_date,
        )
        return False
    actual_close, actual_factor = res
    factor_ok = abs(actual_factor - case.expected_adj_factor) < 0.01
    close_drift_pct = abs(actual_close - case.expected_adj_close) / case.expected_adj_close
    close_ok = close_drift_pct < tolerance
    status = "✓" if (factor_ok and close_ok) else "✗"
    log.info(
        "%s %s %s: adj_factor=%.4f (expected %.1f) close=%.2f (expected ~%.2f, drift %.2f%%) — %s",
        status, case.symbol, case.bar_date,
        actual_factor, case.expected_adj_factor,
        actual_close, case.expected_adj_close, close_drift_pct * 100,
        case.note,
    )
    return factor_ok and close_ok


def _check_parity(raw, adjusted, symbol: str) -> bool:
    """Return True if raw row count == adjusted row count for `symbol`."""
    n_raw = raw.scan(row_filter=f"symbol = '{symbol}'").to_arrow().num_rows
    n_adj = adjusted.scan(row_filter=f"symbol = '{symbol}'").to_arrow().num_rows
    ok = n_raw == n_adj
    log.info(
        "%s %s: raw=%d adjusted=%d %s",
        "✓" if ok else "✗", symbol, n_raw, n_adj,
        "" if ok else f"(DELTA {n_adj - n_raw:+d})",
    )
    return ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--symbol", default=None,
        help="Filter checks to one symbol (NVDA, TSLA, AMZN, GOOGL). Omit = all.",
    )
    p.add_argument(
        "--tolerance", type=float, default=0.001,
        help=(
            "Allowed drift between actual close and the baselined reference, "
            "as a fraction (default 0.001 = ±0.1%%). This is a self-consistency "
            "check against snapshot 1634110371703478731's values, so the "
            "tolerance is tight — any real drift indicates a regression."
        ),
    )
    p.add_argument(
        "--skip-parity", action="store_true",
        help="Skip the per-symbol row-count parity check (fast for ad-hoc runs).",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    cat = get_catalog()
    raw_table = cat.load_table(equities_table_id("polygon_raw"))
    adj_table = cat.load_table(equities_table_id("polygon_adjusted"))
    snap = adj_table.current_snapshot()
    snap_id = snap.snapshot_id if snap else None
    total_rows = (
        int(snap.summary.additional_properties.get("total-records", 0))
        if snap else 0
    )
    log.info(
        "polygon_adjusted snapshot %s (total_rows=%d)\n",
        snap_id, total_rows,
    )

    cases = CASES
    if args.symbol:
        cases = [c for c in cases if c.symbol == args.symbol.upper()]
        if not cases:
            log.error("No reference case for symbol %r", args.symbol)
            return 2

    log.info("─── Split-adjustment math (tolerance ±%.2f%%) ───", args.tolerance * 100)
    case_results = [_check_split_case(adj_table, c, args.tolerance) for c in cases]
    case_pass = sum(case_results)
    case_fail = len(case_results) - case_pass

    parity_fail = 0
    if not args.skip_parity:
        log.info("\n─── Per-symbol row-count parity ───")
        parity_symbols = (
            [args.symbol.upper()] if args.symbol else PARITY_SYMBOLS
        )
        parity_results = [
            _check_parity(raw_table, adj_table, s) for s in parity_symbols
        ]
        parity_fail = sum(1 for r in parity_results if not r)

    log.info("\n─── Summary ───")
    log.info("  split cases:  %d pass, %d fail", case_pass, case_fail)
    if not args.skip_parity:
        log.info("  parity check: %d fail", parity_fail)
    total_fail = case_fail + parity_fail
    log.info("  overall: %s", "OK" if total_fail == 0 else f"{total_fail} FAILURE(S)")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
