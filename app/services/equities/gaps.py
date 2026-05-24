"""
Gap detection for v2 `equities.*` Iceberg tables.

Same algorithm the v1 bronze gap detector used: ET-vs-UTC reasoning
matters because Polygon flat-files mark trading-day-N after-hours
bars with a UTC timestamp that falls on calendar day N+1. Bucketing
by UTC date would falsely advance the "latest day" counter and make
the gap detector miss the next trading day.

Used by:
  - `scripts/polygon_history_backfill.py` (CV3) — pre-computes skip
    set so re-runs of the same window are no-ops.
  - `app/services/ingest/nightly_polygon_refresh.py` (CV7) — cuts the
    nightly cron over to `equities.polygon_raw`.

The v1 `app.services.bronze.gaps` module was deleted in CV14; this is
now the canonical gap-detection module.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pyiceberg.expressions import GreaterThanOrEqual
from pyiceberg.table import Table

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def latest_loaded_date(table: Table, *, lookback_days: int = 14) -> date | None:
    """Most recent **trading day (ET date)** with ≥1 row in `table`, or
    None if no rows exist in the last `lookback_days` days.

    Cheap: scans only the timestamp column for the lookback window via
    Iceberg's partition pruning. For our 5y × 32-bucket × month layout,
    that's ~1-2 monthly manifest reads — typically under a second.
    """
    since = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    try:
        arrow = table.scan(
            row_filter=GreaterThanOrEqual("timestamp", since.isoformat()),
            selected_fields=("timestamp",),
        ).to_arrow()
    except Exception as exc:
        logger.warning("latest_loaded_date(%s): scan failed: %s", table.name(), exc)
        return None

    if arrow.num_rows == 0:
        return None

    import pyarrow.compute as pc

    max_ts = pc.max(arrow["timestamp"]).as_py()
    if max_ts is None:
        return None
    return max_ts.astimezone(_ET).date()


def yesterday_et() -> date:
    """Trading-day calendar's notion of yesterday — ET basis."""
    return (datetime.now(tz=_ET) - timedelta(days=1)).date()


def missing_weekdays(
    table: Table,
    *,
    through: date | None = None,
    max_lookback_days: int = 14,
) -> list[date]:
    """Weekdays (Mon-Fri) NOT yet ingested into `table`, chronological.

    Window: `(latest_loaded_date(table) + 1 day) .. through`. `through`
    defaults to `yesterday_et()`.

    If the table has no data in the lookback window, returns
    `max_lookback_days` of weekdays counting back from `through` — a
    cold-start fallback that won't try to backfill years of history.
    """
    through = through or yesterday_et()
    latest = latest_loaded_date(table, lookback_days=max_lookback_days)

    if latest is None:
        start = through - timedelta(days=max_lookback_days)
        logger.info(
            "missing_weekdays(%s): no rows in last %dd; cold-start %s..%s",
            table.name(), max_lookback_days, start, through,
        )
    else:
        start = latest + timedelta(days=1)

    if start > through:
        return []

    out: list[date] = []
    d = start
    while d <= through:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def loaded_dates_in_range(
    table: Table,
    *,
    start: date,
    end: date,
) -> set[date]:
    """All trading dates (ET) with ≥1 row in `[start, end]`.

    Used as the `skip_dates` argument to
    `FlatFilesBackfillService.backfill_range()` so re-running the
    backfill on a window that's already partially loaded only fetches
    the missing days. Returns an empty set on scan failure (treated as
    "skip nothing, let the sink handle re-writes") to avoid masking
    Iceberg errors with silent skips.
    """
    # Pad both bounds by one ET day's worth of UTC to catch after-hours
    # bars whose UTC date is end+1.
    since = datetime.combine(start - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    try:
        arrow = table.scan(
            row_filter=GreaterThanOrEqual("timestamp", since.isoformat()),
            selected_fields=("timestamp",),
        ).to_arrow()
    except Exception as exc:
        logger.warning(
            "loaded_dates_in_range(%s): scan failed: %s", table.name(), exc,
        )
        return set()

    if arrow.num_rows == 0:
        return set()

    out: set[date] = set()
    for ts in arrow["timestamp"].to_pylist():
        if ts is None:
            continue
        et_date = ts.astimezone(_ET).date()
        if start <= et_date <= end:
            out.add(et_date)
    return out
