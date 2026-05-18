"""
Adjustment-status verification on actual bronze data.

The probe in `app/services/silver/probes/` verifies what each
provider's API returns. This check goes further: it confirms the
data actually sitting in bronze matches the documented adjustment
status (`BRONZE_*_MINUTE_ADJUSTMENT_STATUS` constant).

Why both: the probe tests the provider API. This check tests our
**ingest pipeline + lake state**. If a writer ever mis-tagged data,
ingested raw bars but called them adjusted (or vice versa), the
probe wouldn't catch it but this audit will.

Strategy: for each bronze.{provider}_minute table, pick a known
recent split that the table covers, look up the pre- vs post-split
bars in bronze, and verify the price ratio matches the documented
adjustment status:
  - raw:            pre/post ratio ≈ split_factor
  - split_adjusted: pre/post ratio ≈ 1.0

The probe library (`KNOWN_PROBES`) is reused so the same canonical
splits drive both verifications.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from app.services.bronze.audit import register_check
from app.services.bronze.audit.base import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    safe_load_table,
)

logger = logging.getLogger(__name__)


# Probe → use for which provider's bronze table.
#
# Polygon: our bronze.polygon_minute coverage starts 2021. NVDA's
#   2024-06-10 10-for-1 split is within range AND we confirmed (via
#   the silver probe) that Polygon flat-files for that period are RAW.
#
# Schwab: bronze.schwab_minute only holds 1-min bars within Schwab
#   REST's ~48-day reach. No pre-curated split fits this window, so we
#   verify against the Schwab REST endpoint directly (the silver probe
#   does this) rather than against bronze rows. The bronze check
#   correctly SKIPs with a clear message.
_PROBE_PER_TABLE: dict[str, str] = {
    "polygon_minute": "nvda_2024_10for1",
    # No probe for schwab_minute — we can't reach back to any known
    # split given Schwab's 48-day 1-min window. The silver probe's
    # `schwab_pricehistory_daily` endpoint covers this empirically.
    # Leaving the key absent makes the check skip with a clear note.
}


@register_check("adjustment_status")
class AdjustmentStatusCheck:
    """Verify bronze rows match documented BRONZE_*_ADJUSTMENT_STATUS.

    Spot-checks recent splits found in `app/services/silver/probes/base.py::KNOWN_PROBES`.
    """

    check_name = "adjustment_status"

    def run(self, table_name: str) -> list[AuditResult]:
        from app.services.bronze import schemas as bronze_schemas
        from app.services.silver.probes.base import KNOWN_PROBES

        table, err = safe_load_table(table_name)
        if err:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message="cannot load table",
                    error=err,
                )
            ]

        # Look up the table's documented adjustment status.
        const_name = f"BRONZE_{table_name.upper()}_ADJUSTMENT_STATUS"
        documented = getattr(bronze_schemas, const_name, None)
        if documented is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=f"no {const_name} declared; cannot verify",
                )
            ]

        # Pick a probe (known historical split) appropriate for this table.
        probe_key = _PROBE_PER_TABLE.get(table_name)
        if probe_key is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=(
                        f"no bronze-side probe configured for {table_name} — "
                        f"this provider's bronze coverage doesn't reach a known split; "
                        f"verify via the silver provider probe instead "
                        f"(scripts/probe_provider_adjustment.py)"
                    ),
                    details={"documented_status": documented},
                )
            ]
        spec = KNOWN_PROBES[probe_key]

        # Fetch pre + post split bars from bronze.
        from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

        try:
            start_ts = datetime(
                spec.pre_split_date.year,
                spec.pre_split_date.month,
                spec.pre_split_date.day,
                tzinfo=timezone.utc,
            )
            # Include the post-split day fully.
            end_ts = datetime(
                spec.post_split_date.year,
                spec.post_split_date.month,
                spec.post_split_date.day,
                23, 59, 59,
                tzinfo=timezone.utc,
            )
            scan = table.scan(
                row_filter=And(
                    EqualTo("symbol", spec.symbol),
                    GreaterThanOrEqual("timestamp", start_ts),
                    LessThan(
                        "timestamp",
                        end_ts + pd.Timedelta(seconds=1).to_pytimedelta(),
                    ),
                ),
                selected_fields=("symbol", "timestamp", "close"),
            )
            arrow = scan.to_arrow()
        except Exception as e:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=f"bronze scan failed: {type(e).__name__}",
                    error=str(e),
                )
            ]

        if arrow.num_rows == 0:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=(
                        f"no {spec.symbol} bars in {table_name} for probe "
                        f"{probe_key} (split {spec.pre_split_date}); "
                        f"table doesn't cover this date"
                    ),
                    details={"probe": probe_key},
                )
            ]

        df = arrow.to_pandas()
        df["date"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("America/New_York").dt.date
        daily = df.sort_values("timestamp").groupby("date").last()

        pre_close = (
            float(daily.loc[spec.pre_split_date, "close"])
            if spec.pre_split_date in daily.index else None
        )
        post_close = (
            float(daily.loc[spec.post_split_date, "close"])
            if spec.post_split_date in daily.index else None
        )

        if pre_close is None or post_close is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=(
                        f"missing close for probe ({spec.pre_split_date}, "
                        f"{spec.post_split_date}) in bronze"
                    ),
                    details={
                        "probe": probe_key,
                        "pre_close": pre_close,
                        "post_close": post_close,
                    },
                )
            ]

        ratio = pre_close / post_close

        # If the table is raw, ratio ≈ split_factor.
        # If split_adjusted, ratio ≈ 1.0 (modulo normal day-over-day move).
        # Tolerance: ±10% — generous because a stock's 1-day move on
        # split day can be a few %.
        tolerance = 0.10
        ratio_expected_if_raw = spec.split_factor
        ratio_expected_if_adj = 1.0

        looks_raw = abs(ratio - ratio_expected_if_raw) / ratio_expected_if_raw <= tolerance
        looks_adj = abs(ratio - ratio_expected_if_adj) / ratio_expected_if_adj <= tolerance

        if documented == "raw":
            expected = "raw"
            ok = looks_raw and not looks_adj
        elif documented == "split_adjusted":
            expected = "split_adjusted"
            ok = looks_adj and not looks_raw
        else:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.FAIL,
                    severity=AuditSeverity.FAIL,
                    message=f"unknown documented adjustment status {documented!r}",
                )
            ]

        details = {
            "probe": probe_key,
            "symbol": spec.symbol,
            "pre_split_date": str(spec.pre_split_date),
            "post_split_date": str(spec.post_split_date),
            "pre_close_in_bronze": pre_close,
            "post_close_in_bronze": post_close,
            "ratio": ratio,
            "expected_ratio_if_raw": ratio_expected_if_raw,
            "expected_ratio_if_split_adjusted": ratio_expected_if_adj,
            "documented_status": documented,
            "looks_raw": looks_raw,
            "looks_split_adjusted": looks_adj,
        }

        if ok:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.OK,
                    severity=AuditSeverity.INFO,
                    message=(
                        f"bronze matches documented status='{expected}' "
                        f"(ratio={ratio:.3f}, expected≈{ratio_expected_if_raw if expected=='raw' else ratio_expected_if_adj})"
                    ),
                    details=details,
                )
            ]

        return [
            AuditResult(
                check=self.check_name,
                table=table_name,
                status=AuditStatus.FAIL,
                severity=AuditSeverity.FAIL,
                message=(
                    f"bronze adjustment status mismatch — documented "
                    f"'{documented}' but ratio={ratio:.3f} (expected "
                    f"{ratio_expected_if_raw if expected=='raw' else ratio_expected_if_adj})"
                ),
                details=details,
            )
        ]
