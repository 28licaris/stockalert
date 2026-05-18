"""
Live-stream freshness audit.

Verifies bronze.{provider}_minute's max(timestamp) for `*-stream`
source-tagged rows is recent (< N minutes stale) during market hours.

This is the bronze-side counterpart to the live_lake_writer (TA-5.7):
if the writer is healthy, bronze should be no more than
~(cycle_minutes + lookback_minutes + 5) minutes stale during market
hours. A stale reading means:
  - The writer crashed or got disabled
  - The Schwab live stream disconnected
  - Or it's outside market hours (correct + flagged as INFO)

Catches the failure mode where the writer silently stops contributing
to bronze, returning silver to the old 24-hour-stale state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.services.bronze.audit import register_check
from app.services.bronze.audit.base import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    safe_load_table,
)

logger = logging.getLogger(__name__)


# Per-table mapping: which `source` value indicates a live-stream row
# in this bronze table. Tables without a live stream are skipped
# silently (no error).
_LIVE_SOURCE_TAGS: dict[str, str] = {
    "schwab_minute": "schwab-stream",
}


# Tolerable staleness during market hours, in minutes. Calibrated to:
#   - live_lake_writer cycle = 5 min
#   - lookback overlap = 15 min
#   - 1-min safety margin (writer skips the current minute)
#   - 5-min slop for cycle scheduling jitter, S3 write latency, etc.
# = 26 minutes. We round to 30 for headroom.
DEFAULT_MAX_STALE_MINUTES = 30

# US Eastern is the trading-day reference. Market hours below are the
# regular session (RTH) — extended hours produce bars too but with much
# lower volume; staleness during pre/post-market isn't a red flag.
_ET = ZoneInfo("America/New_York")
_RTH_OPEN_HOUR = 9
_RTH_OPEN_MIN = 30
_RTH_CLOSE_HOUR = 16
_RTH_CLOSE_MIN = 0


def _is_rth(now_utc: datetime) -> bool:
    """True if `now_utc` falls within Regular Trading Hours (ET).

    Conservative: only Mon-Fri 9:30am-4pm ET. Holidays will produce a
    WARN (no fresh data on a market holiday is expected; the operator
    sees the indicator + ignores it). False positives at holiday cost
    a manual ack; we can refine with a calendar later if it's noisy.
    """
    now_et = now_utc.astimezone(_ET)
    if now_et.weekday() >= 5:   # Sat=5, Sun=6
        return False
    open_t = now_et.replace(
        hour=_RTH_OPEN_HOUR, minute=_RTH_OPEN_MIN, second=0, microsecond=0,
    )
    close_t = now_et.replace(
        hour=_RTH_CLOSE_HOUR, minute=_RTH_CLOSE_MIN, second=0, microsecond=0,
    )
    return open_t <= now_et < close_t


@register_check("live_freshness")
class LiveFreshnessCheck:
    """Verify the live-stream's footprint in bronze is recent.

    During RTH: max(timestamp) of *-stream-tagged rows should be within
    `DEFAULT_MAX_STALE_MINUTES` of now. Outside RTH: INFO-only (expected).
    """

    check_name = "live_freshness"

    def run(self, table_name: str) -> list[AuditResult]:
        live_tag = _LIVE_SOURCE_TAGS.get(table_name)
        if live_tag is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    severity=AuditSeverity.INFO,
                    message=f"no live-stream tag configured for {table_name}",
                )
            ]

        table, err = safe_load_table(table_name)
        if err:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    severity=AuditSeverity.INFO,
                    message="cannot load table",
                    error=err,
                )
            ]

        # Find max(timestamp) among rows with source = live_tag.
        try:
            from pyiceberg.expressions import EqualTo

            scan = table.scan(
                row_filter=EqualTo("source", live_tag),
                selected_fields=("timestamp",),
            )
            arrow = scan.to_arrow()
        except Exception as e:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    severity=AuditSeverity.INFO,
                    message=f"scan failed: {type(e).__name__}",
                    error=str(e),
                )
            ]

        if arrow.num_rows == 0:
            # No live-stream-tagged rows at all. During RTH, that's a
            # FAIL (writer not running). Outside RTH, INFO (might be
            # before the writer's first cycle of the trading day).
            now = datetime.now(timezone.utc)
            is_rth = _is_rth(now)
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.FAIL if is_rth else AuditStatus.WARN,
                    severity=AuditSeverity.FAIL if is_rth else AuditSeverity.WARN,
                    message=(
                        f"no '{live_tag}' rows in {table_name} — "
                        f"live_lake_writer may not be running"
                    ),
                    details={
                        "live_tag": live_tag,
                        "currently_rth": is_rth,
                    },
                )
            ]

        # Compute max timestamp + staleness.
        import pyarrow.compute as pc
        try:
            max_ts = pc.max(arrow["timestamp"]).as_py()
        except Exception as e:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=f"max() failed: {type(e).__name__}",
                    error=str(e),
                )
            ]

        if max_ts is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.WARN,
                    severity=AuditSeverity.WARN,
                    message="max(timestamp) returned None",
                )
            ]

        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        stale_minutes = (now - max_ts).total_seconds() / 60.0
        is_rth = _is_rth(now)

        details = {
            "live_tag": live_tag,
            "max_timestamp": max_ts.isoformat(),
            "stale_minutes": round(stale_minutes, 1),
            "max_stale_minutes": DEFAULT_MAX_STALE_MINUTES,
            "currently_rth": is_rth,
            "rows_with_live_tag": arrow.num_rows,
        }

        if stale_minutes > DEFAULT_MAX_STALE_MINUTES:
            if is_rth:
                # In market hours, this is broken.
                return [
                    AuditResult(
                        check=self.check_name,
                        table=table_name,
                        status=AuditStatus.FAIL,
                        severity=AuditSeverity.FAIL,
                        message=(
                            f"live-stream tag in bronze is {stale_minutes:.1f}min "
                            f"stale (RTH; max allowed {DEFAULT_MAX_STALE_MINUTES})"
                        ),
                        details=details,
                    )
                ]
            else:
                # Outside RTH, staleness is expected up to ~17.5h (overnight)
                # or longer (weekend / holiday). Surface as INFO so the
                # operator sees the timestamp without alarms.
                return [
                    AuditResult(
                        check=self.check_name,
                        table=table_name,
                        status=AuditStatus.OK,
                        severity=AuditSeverity.INFO,
                        message=(
                            f"live-stream {stale_minutes:.1f}min stale "
                            f"(outside RTH; expected)"
                        ),
                        details=details,
                    )
                ]

        return [
            AuditResult(
                check=self.check_name,
                table=table_name,
                status=AuditStatus.OK,
                severity=AuditSeverity.INFO,
                message=(
                    f"live-stream fresh: max(ts) = {max_ts.isoformat()} "
                    f"({stale_minutes:.1f}min stale)"
                ),
                details=details,
            )
        ]
