"""
MCP tools — data-quality observability for agents.

ML reproducibility is **the** load-bearing concern for an agent
running training jobs. Before a backtest or feature-engineering
pass, the agent should be able to ask: "is the data complete for
this window?", "where are the gaps?", "is bronze caught up?"
These tools answer those questions.

Backed by `app.db.queries.coverage_async` / `find_intraday_gaps_async`
for CH-side coverage and the Iceberg `inspect` API for bronze-side
freshness/stats.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.schemas import (
    BronzeTableStats,
    CoverageReport,
    GapReport,
    IntradayGap,
)

logger = logging.getLogger(__name__)


# Approximate bars-per-regular-session-day per interval. Used to
# compute coverage_pct. Conservative (regular hours only) so the
# percentage doesn't penalize datasets that exclude extended-hours
# bars by design.
_REGULAR_SESSION_BARS_PER_DAY = {
    "1m": 390,    # 6.5h * 60
    "5m": 78,     # 6.5h * 12
    "15m": 26,
    "30m": 13,
    "1h": 7,
    "4h": 2,
    "1d": 1,
}


def _expected_bars(interval: str, start: datetime, end: datetime) -> Optional[int]:
    """
    Approximate bar count expected in `[start, end]` at `interval`,
    assuming regular-session-only weekdays. Returns None for unsupported
    intervals or when the window is degenerate.
    """
    per_day = _REGULAR_SESSION_BARS_PER_DAY.get(interval)
    if per_day is None or end <= start:
        return None
    # Count weekdays in the window.
    days = (end.date() - start.date()).days + 1
    weekdays = 0
    cur = start.date()
    for _ in range(days):
        if cur.weekday() < 5:
            weekdays += 1
        cur = cur.fromordinal(cur.toordinal() + 1)
    return weekdays * per_day


@mcp.tool()
async def get_coverage(
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str = "1m",
) -> CoverageReport:
    """Data-completeness summary for a symbol in a time window.

    USE WHEN: an agent is validating a training/backtest window
    before running on it. Returns actual vs expected bar count plus
    first/last timestamps so the agent can decide "is this dataset
    complete enough."

    Args:
        symbol: Ticker.
        start: Window start, inclusive (naive treated as UTC).
        end: Window end, inclusive.
        interval: '1m' (default, only supported by the underlying
            CH query today), '5m', or '1d'. Other intervals return
            actual_bars only, with coverage_pct=None.

    Returns:
        CoverageReport with actual_bars (from CH), expected_bars
        (regular-session estimate), coverage_pct (or None), and the
        first/last bar timestamps observed in the window.
    """
    with tool_call("get_coverage", symbol=symbol, interval=interval):
        from app.db import queries

        if interval == "1m":
            cov = await queries.coverage_async(symbol, start, end)
        elif interval == "5m":
            cov = await queries.coverage_5m_async(symbol, start, end)
        elif interval == "1d":
            cov = await queries.daily_coverage_async(symbol, start, end)
        else:
            cov = {"bar_count": 0, "earliest": None, "latest": None}

        actual = int(cov.get("bar_count", 0) or 0)
        expected = _expected_bars(interval, start, end)
        pct = None
        if expected and expected > 0:
            pct = round(actual / expected, 4)

        return CoverageReport(
            symbol=symbol.upper(),
            start=start,
            end=end,
            interval=interval,
            actual_bars=actual,
            expected_bars=expected,
            coverage_pct=pct,
            first_bar=cov.get("earliest"),
            last_bar=cov.get("latest"),
        )


@mcp.tool()
async def find_intraday_gaps(
    symbol: str,
    start: datetime,
    end: datetime,
    min_gap_minutes: int = 5,
) -> GapReport:
    """List contiguous missing-bar ranges for a symbol's 1-min bars.

    USE WHEN: coverage came back below 100% and an agent wants to
    locate the gaps specifically — "is the gap one bad hour, or
    100 random missing minutes?" Drives intelligent re-fill decisions.

    Args:
        symbol: Ticker.
        start: Window start, inclusive.
        end: Window end, inclusive.
        min_gap_minutes: Ignore gaps shorter than this many minutes.
            Default 5 — short gaps (one or two missing prints during
            a low-volume minute) are common and usually not worth
            re-fetching.

    Returns:
        GapReport with gaps=list[IntradayGap], each with
        start/end/minutes. Plus `total_missing_minutes` across all gaps.
        Empty list = complete data.
    """
    with tool_call("find_intraday_gaps", symbol=symbol, min_gap_minutes=min_gap_minutes):
        from app.db import queries

        raw_gaps = await queries.find_intraday_gaps_async(
            symbol, start, end, min_gap_minutes=min_gap_minutes,
        )
        gaps = [
            IntradayGap(
                start=g["start"], end=g["end"], minutes=int(g.get("minutes", 0) or 0),
            )
            for g in raw_gaps
        ]
        total = sum(g.minutes for g in gaps)
        return GapReport(
            symbol=symbol.upper(),
            start=start,
            end=end,
            interval="1m",
            gaps=gaps,
            total_missing_minutes=total,
        )


@mcp.tool()
def get_bronze_table_stats(table: str = "polygon_minute") -> BronzeTableStats:
    """Bronze Iceberg table stats — row count, file count, snapshot, size.

    USE WHEN: an agent is verifying lake state before a training run —
    "how many rows do we actually have in polygon_minute?", "did the
    last nightly write produce a new snapshot?"

    Args:
        table: Short table name (no namespace). Common values:
            'polygon_minute' (default), 'schwab_minute'.

    Returns:
        BronzeTableStats with row count, file count, total bytes,
        current Iceberg snapshot ID, and the table's last-updated
        timestamp. On any error (unknown table, AWS auth, etc.) the
        `error` field is populated and other fields are None.

    Cost: 100-500ms. Reads Iceberg metadata only — no scan of
    actual data files. Cheap regardless of table size.
    """
    with tool_call("get_bronze_table_stats", table=table):
        try:
            from app.config import settings
            from app.services.iceberg_catalog import get_catalog

            table_id = f"{settings.iceberg_glue_database}.{table}"
            t = get_catalog().load_table(table_id)
            snapshot = t.current_snapshot()
            summary = snapshot.summary if snapshot else {}

            return BronzeTableStats(
                table_name=table,
                namespace=settings.iceberg_glue_database,
                total_records=int(summary.get("total-records", 0)) if summary else None,
                file_count=int(summary.get("total-data-files", 0)) if summary else None,
                total_size_bytes=int(summary.get("total-files-size", 0)) if summary else None,
                current_snapshot_id=str(snapshot.snapshot_id) if snapshot else None,
                last_updated=(
                    datetime.fromtimestamp(snapshot.timestamp_ms / 1000, tz=timezone.utc)
                    if snapshot else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.warning("get_bronze_table_stats(%s) failed: %s", table, exc)
            return BronzeTableStats(
                table_name=table,
                namespace="stock_lake",
                error=f"{type(exc).__name__}: {exc}",
            )
