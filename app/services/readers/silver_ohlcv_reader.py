"""
SilverOhlcvReader — read service for `silver.ohlcv_1m` + `silver.bar_quality`.

Reads the canonical, provider-merged, corp-action-adjusted OHLCV table
(produced by `app/services/silver/ohlcv/build.py`). Per the consumer
contract ([silver_layer_plan §"The consumer contract"](../../../docs/silver_layer_plan.md)),
every consumer (chart, screener, indicator, backtest, MCP tool) reads
silver — **never bronze directly**.

This is the CH-independent path: agents and ML pipelines reading
1-minute history go through this reader and never touch ClickHouse.
Snapshot-pinnable for reproducibility.

Two read methods:
  - `get_bars(symbol, start, end, *, adjusted=True)` — windowed OHLCV
  - `get_bar_quality(symbol, since, until)` — per-(symbol, date) audit

Design contract (mirrors `CorpActionsReader`):
  - Pure read; no writes; no global state beyond the catalog handle.
  - Pydantic shape is what HTTP routes + MCP tools both surface —
    single contract, two surfaces.
  - Filters push down to Iceberg (month partition prune +
    symbol-sorted file skip).
  - Cold-start safe: returns empty result if the silver table doesn't
    exist yet (initial system state before any silver_ohlcv_build run).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan, LessThanOrEqual
from pyiceberg.table import Table

from app.services.iceberg_catalog import get_catalog
from app.services.readers.schemas import (
    BarQualityResponse,
    BarQualityRow,
    SilverBarsResponse,
)
from app.services.silver.schemas import SilverBar, silver_table_id

logger = logging.getLogger(__name__)


_OHLCV_TABLE_NAME = "ohlcv_1m"
_BAR_QUALITY_TABLE_NAME = "bar_quality"


class SilverOhlcvReader:
    """Read silver.ohlcv_1m + silver.bar_quality via PyIceberg.

    Construct via `from_settings()` for production; pass `catalog` /
    `ohlcv_table` / `bar_quality_table` explicitly for tests.
    """

    def __init__(
        self,
        *,
        catalog=None,
        ohlcv_table: Optional[Table] = None,
        bar_quality_table: Optional[Table] = None,
    ) -> None:
        self._catalog = catalog
        self._ohlcv_table = ohlcv_table
        self._bar_quality_table = bar_quality_table

    @classmethod
    def from_settings(cls) -> "SilverOhlcvReader":
        return cls()

    def _get_catalog(self):
        if self._catalog is None:
            self._catalog = get_catalog()
        return self._catalog

    def _get_ohlcv_table(self) -> Table:
        if self._ohlcv_table is None:
            self._ohlcv_table = self._get_catalog().load_table(
                silver_table_id(_OHLCV_TABLE_NAME),
            )
        return self._ohlcv_table

    def _get_bar_quality_table(self) -> Table:
        if self._bar_quality_table is None:
            self._bar_quality_table = self._get_catalog().load_table(
                silver_table_id(_BAR_QUALITY_TABLE_NAME),
            )
        return self._bar_quality_table

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> SilverBarsResponse:
        """Read 1-minute silver bars for `symbol` in `[start, end)`.

        `start`/`end` are tz-aware UTC datetimes (caller responsibility;
        naive datetimes are upgraded to UTC defensively).

        Each bar carries one set of OHLCV columns — split-adjusted
        canonical view. That's what chart, indicators, backtests,
        screener, and ML all consume.

        **Need raw prices** (trade-tape replay, fill reconciliation)?
        Multiply silver values by F(symbol, bar_date), where F is the
        cumulative split factor for ex_date > bar_date. Read
        silver.corp_actions via CorpActionsReader and apply the math
        client-side; see `app/services/silver/ohlcv/normalize.py` for
        reference. Silver intentionally doesn't carry redundant raw
        columns.

        Edge cases:
          - Unknown / empty symbol → empty `bars`, count=0.
          - silver.ohlcv_1m doesn't exist yet → empty `bars`.
          - No bars in window → empty `bars`.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return SilverBarsResponse(
                symbol=symbol or "",
                start=_coerce_utc(start),
                end=_coerce_utc(end),
                snapshot_id=None,
                bars=[],
                count=0,
            )

        start_utc = _coerce_utc(start)
        end_utc = _coerce_utc(end)

        try:
            table = self._get_ohlcv_table()
        except Exception as e:
            logger.warning(
                "SilverOhlcvReader: silver.ohlcv_1m not loadable (%s); "
                "returning empty result", e,
            )
            return SilverBarsResponse(
                symbol=sym, start=start_utc, end=end_utc,
                snapshot_id=None, bars=[], count=0,
            )

        row_filter = And(
            EqualTo("symbol", sym),
            GreaterThanOrEqual("timestamp", start_utc),
            LessThan("timestamp", end_utc),
        )

        try:
            scan = table.scan(row_filter=row_filter)
            arrow = scan.to_arrow()
        except Exception as e:
            logger.warning(
                "SilverOhlcvReader: scan failed for %s [%s..%s]: %s",
                sym, start_utc, end_utc, e,
            )
            return SilverBarsResponse(
                symbol=sym, start=start_utc, end=end_utc,
                snapshot_id=None, bars=[], count=0,
            )

        bars = self._arrow_to_bars(arrow)

        snap = table.current_snapshot()
        snap_id = str(snap.snapshot_id) if snap else None

        return SilverBarsResponse(
            symbol=sym,
            start=start_utc,
            end=end_utc,
            snapshot_id=snap_id,
            bars=bars,
            count=len(bars),
        )

    def get_bar_quality(
        self,
        symbol: str,
        *,
        since: Optional[date] = None,
        until: Optional[date] = None,
    ) -> BarQualityResponse:
        """Read per-(symbol, date) quality rows from silver.bar_quality.

        Bounds are inclusive on `date`. Returns empty rows if the
        silver.bar_quality table doesn't exist yet OR no rows match.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return BarQualityResponse(
                symbol=symbol or "", since=since, until=until,
                snapshot_id=None, rows=[], count=0,
            )

        try:
            table = self._get_bar_quality_table()
        except Exception as e:
            logger.warning(
                "SilverOhlcvReader: silver.bar_quality not loadable (%s); "
                "returning empty result", e,
            )
            return BarQualityResponse(
                symbol=sym, since=since, until=until,
                snapshot_id=None, rows=[], count=0,
            )

        clauses = [EqualTo("symbol", sym)]
        if since is not None:
            clauses.append(GreaterThanOrEqual("date", since))
        if until is not None:
            clauses.append(LessThanOrEqual("date", until))
        row_filter = clauses[0] if len(clauses) == 1 else And(*clauses)

        try:
            scan = table.scan(row_filter=row_filter)
            arrow = scan.to_arrow()
        except Exception as e:
            logger.warning(
                "SilverOhlcvReader: bar_quality scan failed for %s: %s",
                sym, e,
            )
            return BarQualityResponse(
                symbol=sym, since=since, until=until,
                snapshot_id=None, rows=[], count=0,
            )

        rows = self._arrow_to_quality_rows(arrow)

        snap = table.current_snapshot()
        snap_id = str(snap.snapshot_id) if snap else None

        return BarQualityResponse(
            symbol=sym, since=since, until=until,
            snapshot_id=snap_id, rows=rows, count=len(rows),
        )

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _arrow_to_bars(arrow) -> list[SilverBar]:
        """Convert PyArrow Table → sorted list[SilverBar]."""
        if arrow.num_rows == 0:
            return []

        # to_pylist preserves dict shape; sort by timestamp for
        # deterministic output (consumers rely on temporal ordering).
        rows = arrow.to_pylist()
        rows.sort(key=lambda r: r["timestamp"])

        out: list[SilverBar] = []
        for r in rows:
            ts = r.get("timestamp")
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ing_ts = r.get("ingestion_ts")
            if ing_ts is not None and ing_ts.tzinfo is None:
                ing_ts = ing_ts.replace(tzinfo=timezone.utc)

            # sources_seen is stored as CSV string; promote back to list.
            seen_raw = r.get("sources_seen") or ""
            sources_seen = [s for s in seen_raw.split(",") if s] if seen_raw else []

            # The silver Arrow schema permits NULLs on price columns; the
            # Pydantic SilverBar declares them as non-Optional float for
            # the canonical (post-merge) row. Skip rows missing any OHLC
            # (would be an upstream bug worth surfacing rather than
            # silently 0-filling).
            if any(
                r.get(c) is None for c in ("open", "high", "low", "close")
            ):
                logger.debug(
                    "SilverOhlcvReader: skipping row with NULL OHLC for "
                    "%s @ %s", r.get("symbol"), ts,
                )
                continue

            out.append(
                SilverBar(
                    symbol=r["symbol"],
                    timestamp=ts,
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=r.get("volume") or 0,
                    vwap=r.get("vwap"),
                    trade_count=r.get("trade_count"),
                    source_provider=r.get("source_provider") or "unknown",
                    sources_seen=sources_seen,
                    ingestion_ts=ing_ts,
                    ingestion_run_id=r.get("ingestion_run_id"),
                )
            )
        return out

    @staticmethod
    def _arrow_to_quality_rows(arrow) -> list[BarQualityRow]:
        if arrow.num_rows == 0:
            return []
        rows = arrow.to_pylist()
        rows.sort(key=lambda r: r["date"])

        out: list[BarQualityRow] = []
        for r in rows:
            providers_csv = r.get("providers_seen") or ""
            providers_list = (
                [p for p in providers_csv.split(",") if p]
                if providers_csv else []
            )
            ing_ts = r.get("ingestion_ts")
            if ing_ts is not None and ing_ts.tzinfo is None:
                ing_ts = ing_ts.replace(tzinfo=timezone.utc)

            out.append(
                BarQualityRow(
                    symbol=r["symbol"],
                    date=r["date"],
                    expected_bars=r.get("expected_bars"),
                    actual_bars=r.get("actual_bars"),
                    gap_count=r.get("gap_count"),
                    max_gap_minutes=r.get("max_gap_minutes"),
                    providers_seen=providers_list,
                    disagreement_count=r.get("disagreement_count"),
                    backfill_attempts=r.get("backfill_attempts"),
                    ingestion_ts=ing_ts,
                    ingestion_run_id=r.get("ingestion_run_id"),
                )
            )
        return out


def _coerce_utc(dt: datetime) -> datetime:
    """Defensive: upgrade naive datetimes to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
