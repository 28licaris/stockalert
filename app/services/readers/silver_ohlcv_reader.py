"""
SilverOhlcvReader — read service for adjusted OHLCV history.

CV11 (Phase 1C): retargeted from `silver.ohlcv_1m` (built nightly by
the legacy silver pipeline) to the v2 canonical store
`equities.polygon_adjusted` (built weekly by the Spark adjustment job
in CV5/CV6). Same adjusted-OHLCV semantics, different storage layer.

The class name + module name are kept stable to avoid cascading
caller rewrites; the rename pass happens in CV14 when the silver
module is deleted. The public API (`get_bars`, `SilverBarsResponse`,
`SilverBar`) is unchanged — every consumer (chart, screener,
indicator, backtest, MCP tool, the CH cold-loader in
silver_to_ch_backfill) keeps working with no change.

`get_bar_quality()` returns an empty response post-CV11 — the v1
bar_quality audit table has no v2 equivalent yet (data-integrity
invariants in v2 are enforced by the Spark adjustment job + corp-
actions ingest, not by a downstream audit table). When v2 needs a
quality column it'll surface as a column on polygon_adjusted, not a
separate table.

This is the CH-independent path: agents and ML pipelines reading
1-minute history go through this reader and never touch ClickHouse.
Snapshot-pinnable for reproducibility.

Design contract (mirrors `CorpActionsReader` post-CV10):
  - Pure read; no writes; no global state beyond the catalog handle.
  - Pydantic shape is what HTTP routes + MCP tools both surface —
    single contract, two surfaces.
  - Filters push down to Iceberg — CV1's POLYGON_ADJUSTED_PARTITION
    uses bucket(32, symbol) + month(timestamp), so single-symbol
    queries scan ~1/32 of each month's data.
  - Cold-start safe: returns empty result if equities.polygon_adjusted
    doesn't exist yet (initial system state before the first
    polygon_adjustment_job run).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan, LessThanOrEqual
from pyiceberg.table import Table

from app.services.equities.schemas import equities_table_id
from app.services.iceberg_catalog import get_catalog
from app.services.readers.schemas import (
    BarQualityResponse,
    BarQualityRow,
    SilverBarsResponse,
)
from app.services.equities.models import SilverBar

logger = logging.getLogger(__name__)


_OHLCV_TABLE_NAME = "polygon_adjusted"


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
                equities_table_id(_OHLCV_TABLE_NAME),
            )
        return self._ohlcv_table

    def _get_bar_quality_table(self) -> Table:
        # No v2 equivalent of silver.bar_quality. Callers of
        # get_bar_quality() get an empty BarQualityResponse via the
        # short-circuit there; this method is kept for tests that
        # construct the reader with an explicit table fixture.
        if self._bar_quality_table is None:
            raise FileNotFoundError(
                "bar_quality table has no v2 equivalent; callers should "
                "use get_bar_quality() which short-circuits to empty"
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
        """Read 1-minute adjusted OHLCV bars for `symbol` in `[start, end)`.

        Source: `equities.polygon_adjusted` (CV1's table populated by
        the Spark adjustment job in CV5/CV6). `start`/`end` are tz-aware
        UTC datetimes (caller responsibility; naive datetimes upgraded
        to UTC defensively).

        Each bar carries one set of OHLCV columns — split-adjusted
        canonical view. That's what chart, indicators, backtests,
        screener, and ML all consume.

        **Need raw prices** (trade-tape replay, fill reconciliation)?
        Multiply adjusted values by `adj_factor` (stored on every
        polygon_adjusted row per CV1's POLYGON_ADJUSTED_SCHEMA).
        adj_factor is the cumulative future-splits factor at the bar's
        timestamp, so `raw_value = adj_value × adj_factor`.

        Edge cases:
          - Unknown / empty symbol → empty `bars`, count=0.
          - equities.polygon_adjusted doesn't exist yet → empty `bars`.
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
                "SilverOhlcvReader: equities.polygon_adjusted not "
                "loadable (%s); returning empty result", e,
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
        """Per-(symbol, date) quality rows from v1's silver.bar_quality.

        Post-CV11: NO V2 EQUIVALENT. Returns an empty response. v2 enforces
        data-integrity invariants via the Spark adjustment job + corp-
        actions ingest (CV5/CV9) rather than a downstream audit table.
        If/when a v2 quality column is needed, it'll surface on
        polygon_adjusted directly.

        Callers (none today; reserved surface for future MCP tooling)
        get a well-formed empty BarQualityResponse so they can switch
        on `count == 0` if they need the absence signal.
        """
        sym = (symbol or "").strip().upper()
        # No v2 table; tests / callers passing an explicit table fixture
        # can still hit the legacy path below — but production code paths
        # short-circuit here.
        if self._bar_quality_table is None:
            return BarQualityResponse(
                symbol=sym or symbol or "", since=since, until=until,
                snapshot_id=None, rows=[], count=0,
            )

        if not sym:
            return BarQualityResponse(
                symbol=symbol or "", since=since, until=until,
                snapshot_id=None, rows=[], count=0,
            )

        try:
            table = self._get_bar_quality_table()
        except Exception as e:
            logger.warning(
                "SilverOhlcvReader: bar_quality table fixture failed (%s); "
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

            # Provider column rename: v1 silver had `source_provider`;
            # v2 equities.polygon_adjusted has `source` (CV1 schema).
            # Read whichever column is present so the reader survives
            # the v1-test silver fixtures + the v2 production path.
            source_provider = (
                r.get("source_provider")
                or r.get("source")
                or "unknown"
            )
            # sources_seen is v1-silver-only (a multi-provider merge
            # artifact). v2 equities.polygon_adjusted is single-provider
            # by definition (polygon), so sources_seen is [].
            seen_raw = r.get("sources_seen") or ""
            sources_seen = [s for s in seen_raw.split(",") if s] if seen_raw else []

            # The Iceberg schema permits NULLs on price columns; the
            # Pydantic SilverBar declares them as non-Optional float for
            # the canonical row. Skip rows missing any OHLC (upstream
            # bug worth surfacing rather than silently 0-filling).
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
                    source_provider=source_provider,
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
