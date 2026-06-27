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
  - `app/services/ingest/nightly_equities_polygon_refresh.py` (CV7) — cuts the
    nightly cron over to `equities.polygon_raw`.

The v1 `app.services.bronze.gaps` module was deleted in CV14; this is
now the canonical gap-detection module.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pyiceberg.expressions import And, GreaterThanOrEqual, LessThan
from pyiceberg.table import Table

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def latest_loaded_date(table: Table, *, lookback_days: int = 14) -> date | None:
    """Most recent **trading day (ET date)** with ≥1 row in `table`, or
    None if the table is genuinely EMPTY in the last `lookback_days` days.

    Cheap: scans only the timestamp column for the lookback window via
    Iceberg's partition pruning. For our 5y × 32-bucket × month layout,
    that's ~1-2 monthly manifest reads — typically under a second.

    NO SILENT FAILURES: a scan error (e.g. S3 ACCESS_DENIED, transient
    lake outage) is RAISED, not swallowed into a None. The two outcomes
    are semantically different — "no data yet" (None) vs "I couldn't
    read the data" (error) — and conflating them previously let an
    ACCESS_DENIED masquerade as cold-start, triggering a blind 14-day
    re-fetch on every nightly run. Callers decide how to degrade (the
    nightly skips that run; the freshness reader returns None).
    """
    since = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    # Let scan errors propagate — the caller must distinguish them from
    # a genuinely-empty result.
    arrow = table.scan(
        row_filter=GreaterThanOrEqual("timestamp", since.isoformat()),
        selected_fields=("timestamp",),
    ).to_arrow()

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

    try:
        latest = latest_loaded_date(table, lookback_days=max_lookback_days)
    except Exception as exc:
        # A coverage-scan failure (S3 ACCESS_DENIED, transient lake
        # outage, etc.) must NOT be silently read as "no data → cold
        # start" — that would blindly re-fetch `max_lookback_days` every
        # run. Fail loud and skip this run; the underlying read error is
        # the thing to fix, not paper over with a redundant backfill.
        logger.error(
            "missing_weekdays(%s): coverage scan FAILED (%s) — skipping this "
            "run instead of a blind %dd cold-start re-fetch. Fix the lake "
            "read error (creds/perms/outage) and the next run self-heals.",
            table.name(), exc, max_lookback_days,
        )
        return []

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

    Streams record batches rather than buffering the entire timestamp
    column — a 20y whole-market scan is ~2B rows × 8B = ~17 GB if
    materialized, which OOM-kills 15 GB workers. Result set is bounded
    by the window size (≤7300 trading days for 20y) so it stays tiny.
    """
    # Lower bound: pad by 1 ET day to catch after-hours bars (UTC date = ET+1).
    # Upper bound: pad by 1 ET day for symmetry. Both bounds let Iceberg
    # partition-prune to the requested window — without an upper bound the
    # filter degenerates to "scan everything from start onward", which on a
    # multi-billion-row table OOM-kills the worker even with batched reads.
    since = datetime.combine(start - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    until = datetime.combine(end + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    out: set[date] = set()
    try:
        scan = table.scan(
            row_filter=And(
                GreaterThanOrEqual("timestamp", since.isoformat()),
                LessThan("timestamp", until.isoformat()),
            ),
            selected_fields=("timestamp",),
        )
        reader = scan.to_arrow_batch_reader()
        for batch in reader:
            for ts in batch.column("timestamp").to_pylist():
                if ts is None:
                    continue
                et_date = ts.astimezone(_ET).date()
                if start <= et_date <= end:
                    out.add(et_date)
    except Exception as exc:
        logger.warning(
            "loaded_dates_in_range(%s): scan failed: %s", table.name(), exc,
        )
        return set()
    return out
