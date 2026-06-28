"""Gap detection for ``futures.schwab_futures``.

Same ET-date reasoning as ``equities.gaps`` (overnight/after-hours bars
carry a UTC timestamp on the next calendar day, so ET-date bucketing is
required), but the CME Globex session calendar differs from equities:
futures trade **Sun-Fri** — only Saturday is fully closed. So the
"missing sessions" enumeration skips Saturday rather than the whole
weekend.

Reuses the table-generic ``latest_loaded_date`` / ``yesterday_et`` from
``equities.gaps`` (re-exported here for callers); only the day-of-week
filter is futures-specific.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from pyiceberg.table import Table

from app.services.equities.gaps import latest_loaded_date, yesterday_et

logger = logging.getLogger(__name__)

__all__ = [
    "is_futures_session_day",
    "latest_loaded_date",
    "missing_futures_sessions",
    "yesterday_et",
]


def is_futures_session_day(d: date) -> bool:
    """True for CME Globex session days. Globex runs Sun 18:00 ET →
    Fri 17:00 ET, so every weekday plus Sunday has bars; only Saturday
    (``weekday() == 5``) is fully dark."""
    return d.weekday() != 5


def missing_futures_sessions(
    table: Table,
    *,
    through: date | None = None,
    max_lookback_days: int = 14,
) -> list[date]:
    """CME session days (Sun-Fri) NOT yet ingested into ``table``,
    chronological.

    Window: ``(latest_loaded_date(table) + 1 day) .. through``; ``through``
    defaults to ``yesterday_et()``. Cold-start (no rows in the lookback
    window) returns up to ``max_lookback_days`` of session days counting
    back from ``through``. A coverage-scan FAILURE returns ``[]`` and logs
    loud (no blind cold-start re-fetch) — same contract as
    ``equities.gaps.missing_weekdays``.
    """
    through = through or yesterday_et()

    try:
        latest = latest_loaded_date(table, lookback_days=max_lookback_days)
    except Exception as exc:
        # A coverage-scan failure must NOT be silently read as "no data →
        # cold start" — that would blindly re-fetch the whole lookback
        # window every run. Fail loud and skip; the next run self-heals.
        logger.error(
            "missing_futures_sessions(%s): coverage scan FAILED (%s) — skipping "
            "this run instead of a blind %dd cold-start re-fetch.",
            table.name(), exc, max_lookback_days,
        )
        return []

    if latest is None:
        start = through - timedelta(days=max_lookback_days)
        logger.info(
            "missing_futures_sessions(%s): no rows in last %dd; cold-start %s..%s",
            table.name(), max_lookback_days, start, through,
        )
    else:
        start = latest + timedelta(days=1)

    if start > through:
        return []

    # Sun–Fri ET calendar dates (skip Saturday). NOTE: we deliberately do
    # NOT use the market_calendar CMES sessions here — exchange_calendars
    # labels CME sessions Mon–Fri (the Sunday-evening Globex session is
    # labelled Monday), but futures.schwab_futures stores bars by ET
    # calendar date where Sunday HAS bars. Using CMES labels would skip
    # Sundays and never backfill them. The marginal cost of requesting the
    # few full-CME-closure days/year (×16 roots) is negligible; correctness
    # of the Sunday session matters more. See docs/market_calendar_spec.md.
    out: list[date] = []
    d = start
    while d <= through:
        if is_futures_session_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out
