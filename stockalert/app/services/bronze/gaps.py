"""
Bronze gap detection — figure out which days are missing from a bronze
Iceberg table so the nightly job can fill them all, not just yesterday.

Used by `nightly_lake_refresh` and `nightly_schwab_refresh` to convert
"run yesterday" → "catch up everything since the last successful day."

Cheap by design: scans only the most recent N days of partitions via
Iceberg's partition pruning. For a 35 GB bronze table with monthly
partitions, that's ~1–2 monthly manifest files — typically under a
second of metadata work.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pyiceberg.expressions import GreaterThanOrEqual
from pyiceberg.table import Table

logger = logging.getLogger(__name__)

# US equities trading day spans (in ET): pre-market 04:00 → after-hours 20:00.
# After-hours bars extend into the NEXT UTC calendar date (e.g. May 14 after-hours
# ends at 2026-05-15 00:00 UTC), so UTC date is the wrong unit for "what
# trading day do we have data for". ET date is the right unit.
_ET = ZoneInfo("America/New_York")


def latest_bronze_date(table: Table, *, lookback_days: int = 14) -> date | None:
    """
    Return the most recent **trading day (ET date)** with ≥1 row in `table`,
    or None if no rows exist in the last `lookback_days` days.

    Why ET and not UTC: Polygon flat files for "May 14 trading day" include
    after-hours bars whose UTC timestamp falls on May 15. Bucketing those
    bars by UTC date would falsely advance the "latest day" counter and
    make the gap detector miss the next trading day. ET date matches the
    flat-file's notion of a trading day exactly.
    """
    since = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    try:
        arrow = table.scan(
            row_filter=GreaterThanOrEqual("timestamp", since.isoformat()),
            selected_fields=("timestamp",),
        ).to_arrow()
    except Exception as exc:
        logger.warning("latest_bronze_date(%s): scan failed: %s", table.name(), exc)
        return None

    if arrow.num_rows == 0:
        return None

    import pyarrow.compute as pc  # local import keeps this module light

    max_ts = pc.max(arrow["timestamp"]).as_py()
    if max_ts is None:
        return None
    # PyIceberg returns timezone-aware datetimes for timestamptz columns.
    # Convert to ET so after-hours bars map to the right trading day.
    return max_ts.astimezone(_ET).date()


def yesterday_et() -> date:
    """The trading-day calendar's notion of yesterday — ET basis."""
    return (datetime.now(tz=_ET) - timedelta(days=1)).date()


def missing_weekdays(
    table: Table,
    *,
    through: date | None = None,
    max_lookback_days: int = 14,
) -> list[date]:
    """
    Return weekdays (Mon-Fri) NOT yet ingested into `table`, ordered
    chronologically. Window:
        (latest_bronze_date(table) + 1 day) .. through

    If `through` is None, default is yesterday in ET (the trading-day calendar).
    If the table has no data in the lookback window, the function returns
    `max_lookback_days` of weekdays going back from `through` — a
    "cold-start" fallback that won't try to backfill years of history.

    Weekend dates are skipped — Polygon flat files and Schwab pricehistory
    don't produce data for them, and asking would just generate noise.
    """
    through = through or yesterday_et()
    latest = latest_bronze_date(table, lookback_days=max_lookback_days)

    if latest is None:
        start = through - timedelta(days=max_lookback_days)
        logger.info(
            "missing_weekdays(%s): no rows in last %dd; cold-start window %s..%s",
            table.name(), max_lookback_days, start, through,
        )
    else:
        start = latest + timedelta(days=1)

    if start > through:
        return []

    out: list[date] = []
    d = start
    while d <= through:
        if d.weekday() < 5:  # Mon–Fri only
            out.append(d)
        d += timedelta(days=1)
    return out
