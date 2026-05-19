#!/usr/bin/env python3
"""
Spot-check silver.ohlcv_1m's split-adjustment math (TA-5.1.7 gate test).

After a fresh silver build, this script reads back a handful of known
(symbol, date) tuples that span well-known splits and asserts the
returned closing price is in the expected ADJUSTED range — NOT the
raw pre-split price.

This is the "did the math actually work?" gate. If silver.corp_actions
was incomplete or the build skipped the factor multiplication, this
script catches it loudly.

**No external dependencies** (no Yahoo HTTP, no rate limits). All
expected values are inlined here from published Yahoo Finance historical
closes (split-adjusted), recorded once at script-write time:
- TSLA 2022-08-24 (one day before 3:1 split):  ~$296   (raw was ~$889)
- NVDA 2024-06-07 (one day before 10:1 split): ~$120.99 (raw was ~$1,209)
- NVDA 2021-07-19 (one day before 4:1 split):  ~$19.55 (raw was ~$782,
      cumulative factor = 4:1 × 10:1 = 40, because the 2024 10:1 split
      also applies to pre-split-date bars as a backward-looking adjustment)
- AMZN 2022-06-03 (one day before 20:1 split): ~$124.79 (raw was ~$2,495)
- GOOGL 2022-07-15 (one day before 20:1 split): ~$112.71 (raw was ~$2,254)

NOTE — AAPL is not checked here. All AAPL splits (7:1 in 2014, 4:1 in 2020)
predate our bronze history window (BRONZE_HISTORY_START = 2021-01-04). Every
AAPL bar we hold was ingested after the splits occurred, so the Polygon raw
price already reflects the post-split price — no backward adjustment is
applied (correctly). There is nothing to verify in our data window.

A tolerance of ±5% is applied to each expected value — accounts for
intraday variance + minute-vs-day-close difference.

Exit codes:
  0 = all spot-checks passed
  2 = one or more failed (silver math is wrong; do NOT proceed to CH load)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

logger = logging.getLogger(__name__)


@dataclass
class SpotCheck:
    """One (symbol, date, expected_adjusted_close) triple to validate."""

    symbol: str
    iso_date: str  # YYYY-MM-DD (the date to query)
    expected_close: float  # Yahoo-published adjusted close
    description: str  # e.g. "one day before AAPL 4:1 split (2020-08-31)"
    tolerance_pct: float = 0.05  # ±5% by default


# Inlined expected values — see module docstring.
#
# Why no AAPL checks: all AAPL splits (7:1/2014, 4:1/2020) predate our
# BRONZE_HISTORY_START (2021-01-04). Bars we hold were ingested post-split,
# so Polygon already returns them at post-split prices — no backward adjustment
# is applied. Nothing to assert in our data window.
SPOT_CHECKS: list[SpotCheck] = [
    SpotCheck(
        symbol="TSLA",
        iso_date="2022-08-24",
        expected_close=296.0,
        description="TSLA day before 3:1 split (raw ~$889 → adj ~$296)",
    ),
    SpotCheck(
        symbol="NVDA",
        iso_date="2024-06-07",
        expected_close=120.99,
        description="NVDA day before 10:1 split (raw ~$1,209 → adj ~$121)",
    ),
    SpotCheck(
        symbol="NVDA",
        iso_date="2021-07-19",
        # Expected = raw ~$782 / (4:1 split 2021-07-20 × 10:1 split 2024-06-10)
        #          = $782 / 40 = $19.55.  The 2024 split is a forward-looking
        # backward adjustment applied to all pre-2024 bars — silver correctly
        # applies ALL future splits to historical bars, not just the nearest one.
        expected_close=19.55,
        description="NVDA day before 4:1 split (raw ~$782 → adj ~$19.55 via 4:1×10:1=40)",
    ),
    SpotCheck(
        symbol="AMZN",
        iso_date="2022-06-03",
        expected_close=124.79,
        description="AMZN day before 20:1 split (raw ~$2,495 → adj ~$124.79)",
    ),
    SpotCheck(
        symbol="GOOGL",
        iso_date="2022-07-15",
        expected_close=112.71,
        description="GOOGL day before 20:1 split (raw ~$2,254 → adj ~$112.71)",
    ),
]


@dataclass
class CheckResult:
    """Outcome of one spot-check."""

    symbol: str
    iso_date: str
    expected: float
    observed: Optional[float] = None
    passed: bool = False
    error: Optional[str] = None
    description: str = ""

    @property
    def deviation_pct(self) -> float:
        if self.observed is None:
            return 0.0
        return abs(self.observed - self.expected) / self.expected * 100


def _check_one(spec: SpotCheck) -> CheckResult:
    """Query silver.ohlcv_1m for the last minute of the target day and
    compare close price against `expected_close`."""
    from app.services.readers.silver_ohlcv_reader import SilverOhlcvReader

    result = CheckResult(
        symbol=spec.symbol,
        iso_date=spec.iso_date,
        expected=spec.expected_close,
        description=spec.description,
    )

    try:
        # Window: the day's last regular-hours hour (20:00 UTC = 4pm ET close).
        # Use a 1-hour window to be tolerant of holiday-shortened days.
        day = datetime.fromisoformat(spec.iso_date).replace(tzinfo=timezone.utc)
        start = day.replace(hour=19, minute=0)
        end = day.replace(hour=21, minute=0)
        reader = SilverOhlcvReader.from_settings()
        resp = reader.get_bars(spec.symbol, start, end)

        if resp.count == 0:
            result.error = (
                f"No silver bars in window [{start.isoformat()}, "
                f"{end.isoformat()}) — symbol/date missing from silver"
            )
            return result

        # Use the close of the last bar in the window (typically 19:59 or
        # 20:00 UTC = market close).
        last_bar = resp.bars[-1]
        result.observed = float(last_bar.close)

        if result.deviation_pct <= spec.tolerance_pct * 100:
            result.passed = True
        else:
            result.error = (
                f"close={result.observed:.2f} deviates "
                f"{result.deviation_pct:.1f}% from expected "
                f"{spec.expected_close:.2f} (tolerance: "
                f"±{spec.tolerance_pct * 100:.0f}%)"
            )
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"

    return result


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--out-json", type=Path, default=None,
        help="Write structured spot-check report to this path.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    started = datetime.now(timezone.utc)
    results: list[CheckResult] = []

    print()
    print("─── silver split-adjustment spot-checks ───")
    print(f"  {'SYMBOL':<6} {'DATE':<12} {'EXPECTED':>10} {'OBSERVED':>10} "
          f"{'DEV %':>6}  STATUS")
    print("-" * 80)

    for spec in SPOT_CHECKS:
        result = _check_one(spec)
        results.append(result)
        obs_str = f"{result.observed:>10.2f}" if result.observed is not None else f"{'?':>10}"
        dev_str = f"{result.deviation_pct:>6.1f}" if result.observed is not None else f"{'?':>6}"
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"  {spec.symbol:<6} {spec.iso_date:<12} {spec.expected_close:>10.2f} "
              f"{obs_str} {dev_str}  {status}")
        if result.error and not result.passed:
            print(f"           ↳ {result.error}")

    print()
    n_pass = sum(1 for r in results if r.passed)
    n_fail = sum(1 for r in results if not r.passed)
    print(f"  PASSED: {n_pass}/{len(results)}")
    print(f"  FAILED: {n_fail}/{len(results)}")
    print()

    if n_fail == 0:
        print("  ✅ All split-adjustment math is correct.")
        print("  Silver build is verified. Safe to proceed to ClickHouse load.")
    else:
        print("  ❌ Silver math is wrong for at least one symbol.")
        print("  DO NOT proceed to ClickHouse load until investigated.")
        print("  Possible causes:")
        print("    - silver.corp_actions missing the relevant split")
        print("    - silver build skipped the factor multiplication")
        print("    - wrong (symbol, date) coverage in silver")
    print()

    if args.out_json:
        payload = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "checks": [asdict(r) for r in results],
            "summary": {"passed": n_pass, "failed": n_fail},
        }
        args.out_json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 0 if n_fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
