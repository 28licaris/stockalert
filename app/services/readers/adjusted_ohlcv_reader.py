"""
AdjustedOhlcvReader — read service for adjusted OHLCV history.

CV11 (Phase 1C): retargeted from `silver.ohlcv_1m` (built nightly by
the legacy silver pipeline) to the v2 canonical store
`equities.polygon_adjusted` (built weekly by the Spark adjustment job
in CV5/CV6). Same adjusted-OHLCV semantics, different storage layer.

The class name + module name are kept stable to avoid cascading
caller rewrites; the rename pass happens in CV14 when the silver
module is deleted. The public API (`get_bars`, `SilverBarsResponse`,
`SilverBar`) is unchanged — every consumer (chart, screener,
indicator, backtest, MCP tool, the CH cold-loader in
lake_to_ch_backfill) keeps working with no change.

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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan, LessThanOrEqual
from pyiceberg.table import Table

from app.services.equities.schemas import equities_table_id
from app.services.iceberg_catalog import get_catalog
from app.services.readers.schemas import (
    AdjustedSymbolsResponse,
    BarQualityResponse,
    BarQualityRow,
    CrossProviderDiffResponse,
    CrossProviderDiffRow,
    SilverBarsResponse,
    SourceCoverage,
    SymbolCoverageResponse,
)
from app.services.equities.models import SilverBar

logger = logging.getLogger(__name__)


_OHLCV_TABLE_NAME = "polygon_adjusted"


class AdjustedOhlcvReader:
    """Read split-adjusted OHLCV bars from the v2 lake.

    Two read paths:

      - `get_bars(symbol, start, end)` — polygon_adjusted only.
        Deep history; lags real-time by up to one weekly Spark run.
      - `get_bars_union(symbol, start, end)` — polygon_adjusted +
        equities.schwab_universe stitched on (symbol, timestamp).
        Use when the window includes "today" and you need both deep
        history AND today's live bars in one response.

    Construct via `from_settings()` for production; pass `catalog` /
    `ohlcv_table` / `bar_quality_table` / `schwab_table` explicitly
    for tests.
    """

    def __init__(
        self,
        *,
        catalog=None,
        ohlcv_table: Optional[Table] = None,
        bar_quality_table: Optional[Table] = None,
        schwab_table: Optional[Table] = None,
    ) -> None:
        self._catalog = catalog
        self._ohlcv_table = ohlcv_table
        self._bar_quality_table = bar_quality_table
        self._schwab_table = schwab_table

    @classmethod
    def from_settings(cls) -> "AdjustedOhlcvReader":
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

    def _get_schwab_table(self) -> Table:
        if self._schwab_table is None:
            self._schwab_table = self._get_catalog().load_table(
                equities_table_id("schwab_universe"),
            )
        return self._schwab_table

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
                "AdjustedOhlcvReader: equities.polygon_adjusted not "
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
                "AdjustedOhlcvReader: scan failed for %s [%s..%s]: %s",
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

    def get_bars_union(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> SilverBarsResponse:
        """Adjusted bars for `[start, end)` UNIONing the two v2 sources.

        Reads BOTH `equities.polygon_adjusted` (canonical adjusted deep
        history) AND `equities.schwab_universe` (live + tip-fill, also
        pre-adjusted with `adj_factor=1.0`) and stitches them on
        `(symbol, timestamp)` with polygon winning duplicates.

        Why polygon wins: the Spark adjustment job (CV5) is the source
        of truth for adj_factor across history. Schwab rows in the
        overlap window are accurate but redundant; choosing polygon
        keeps adj_factor consistent for downstream consumers (chart,
        backtest math).

        Use when:
          - Chart "deep zoom that includes today" — polygon_adjusted
            lags real-time by up to one weekly Spark run; schwab_universe
            fills the trailing window.
          - ML training set whose last day is today's date.
          - Cross-provider continuity validation.

        Cost: two single-symbol bucket-pruned scans (each ~1/32 of the
        relevant months). Wall-clock typically <1s for a 5y window on
        warm cache; the union + sort happens in Python over the
        combined Arrow tables.

        Same return contract as `get_bars()` — `SilverBarsResponse`
        with bars sorted ascending by timestamp.
        """
        sym = (symbol or "").strip().upper()
        start_utc = _coerce_utc(start)
        end_utc = _coerce_utc(end)
        if not sym:
            return SilverBarsResponse(
                symbol=symbol or "",
                start=start_utc, end=end_utc,
                snapshot_id=None, bars=[], count=0,
            )

        # Pull both tables independently — either may be cold-start
        # empty without failing the union.
        polygon_arrow = self._scan_window(
            self._get_ohlcv_table, sym, start_utc, end_utc,
            label="equities.polygon_adjusted",
        )
        schwab_arrow = self._scan_window(
            self._get_schwab_table, sym, start_utc, end_utc,
            label="equities.schwab_universe",
        )

        polygon_bars = self._arrow_to_bars(polygon_arrow) if polygon_arrow is not None else []
        schwab_bars = self._arrow_to_bars(schwab_arrow) if schwab_arrow is not None else []

        # Dedupe on (symbol, timestamp); polygon wins.
        merged: dict[tuple, SilverBar] = {}
        for b in schwab_bars:
            merged[(b.symbol, b.timestamp)] = b
        for b in polygon_bars:
            merged[(b.symbol, b.timestamp)] = b

        bars = sorted(merged.values(), key=lambda b: b.timestamp)

        # Snapshot id reflects the polygon side — that's the canonical
        # adjusted source, and reproducibility-critical consumers care
        # about its pinning. Schwab's snapshot is reported in the
        # `snapshot_id_schwab` metadata if either consumer needs both.
        snap_id: Optional[str] = None
        try:
            snap = self._get_ohlcv_table().current_snapshot()
            snap_id = str(snap.snapshot_id) if snap else None
        except Exception:
            pass

        return SilverBarsResponse(
            symbol=sym,
            start=start_utc,
            end=end_utc,
            snapshot_id=snap_id,
            bars=bars,
            count=len(bars),
        )

    def _scan_window(
        self,
        table_getter,
        symbol: str,
        start_utc: datetime,
        end_utc: datetime,
        *,
        label: str,
    ):
        """Window-scan helper shared by get_bars + get_bars_union.

        Returns the Arrow Table, or None on table-not-loadable /
        scan-failed (caller treats both as "this source contributed
        nothing to the union" — single-source failure must not break
        the other).
        """
        try:
            table = table_getter()
        except Exception as e:
            logger.warning(
                "AdjustedOhlcvReader: %s not loadable (%s); "
                "treating as empty for this read", label, e,
            )
            return None

        row_filter = And(
            EqualTo("symbol", symbol),
            GreaterThanOrEqual("timestamp", start_utc),
            LessThan("timestamp", end_utc),
        )
        try:
            return table.scan(row_filter=row_filter).to_arrow()
        except Exception as e:
            logger.warning(
                "AdjustedOhlcvReader: %s scan failed for %s [%s..%s]: %s",
                label, symbol, start_utc, end_utc, e,
            )
            return None

    def get_symbol_coverage(self, symbol: str) -> SymbolCoverageResponse:
        """Coverage stats for `symbol` across both v2 adjusted sources.

        Returns a SymbolCoverageResponse with per-table SourceCoverage
        for `equities.polygon_adjusted` and `equities.schwab_universe`.
        Each side independently degrades to row_count=0 / Nones when
        the table is cold-start empty or scan fails — never raises.

        Cheap: two single-symbol bucket-pruned timestamp scans (each
        ~1/32 of the table's metadata pages thanks to CV1's bucket
        partitioning). Returns in under a second on warm cache; the
        scan reads only the `timestamp` column, not the full row.

        Used by:
          - The cockpit "is this symbol ready to chart?" check
          - The MCP `get_symbol_coverage` tool agents call before
            queueing a long backtest
          - Operator investigations: "does NVDA have data back to
            2021? Is today's bar in there yet?"
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            empty = SourceCoverage(
                table_name="(empty symbol)", row_count=0,
            )
            return SymbolCoverageResponse(
                symbol=symbol or "",
                polygon_adjusted=empty.model_copy(
                    update={"table_name": "equities.polygon_adjusted"}
                ),
                schwab_universe=empty.model_copy(
                    update={"table_name": "equities.schwab_universe"}
                ),
            )

        polygon = self._coverage_for(
            self._get_ohlcv_table,
            sym,
            table_label="equities.polygon_adjusted",
        )
        schwab = self._coverage_for(
            self._get_schwab_table,
            sym,
            table_label="equities.schwab_universe",
        )
        return SymbolCoverageResponse(
            symbol=sym,
            polygon_adjusted=polygon,
            schwab_universe=schwab,
        )

    def _coverage_for(
        self, table_getter, symbol: str, *, table_label: str,
    ) -> SourceCoverage:
        """Build a SourceCoverage for one (symbol, table) pair.

        Cold-start safe: missing tables / scan failures degrade to
        row_count=0 with None timestamps + a warning log. Never raises.
        """
        try:
            table = table_getter()
        except Exception as e:
            logger.warning(
                "AdjustedOhlcvReader.coverage: %s not loadable (%s); "
                "reporting empty", table_label, e,
            )
            return SourceCoverage(table_name=table_label, row_count=0)

        try:
            arrow = table.scan(
                row_filter=EqualTo("symbol", symbol),
                selected_fields=("timestamp",),
            ).to_arrow()
        except Exception as e:
            logger.warning(
                "AdjustedOhlcvReader.coverage: %s scan failed for %s: %s",
                table_label, symbol, e,
            )
            return SourceCoverage(table_name=table_label, row_count=0)

        row_count = arrow.num_rows
        if row_count == 0:
            return SourceCoverage(table_name=table_label, row_count=0)

        import pyarrow.compute as pc

        earliest = pc.min(arrow["timestamp"]).as_py()
        latest = pc.max(arrow["timestamp"]).as_py()
        if earliest is not None and earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)
        if latest is not None and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)

        snap_id: Optional[str] = None
        try:
            snap = table.current_snapshot()
            if snap is not None:
                snap_id = str(snap.snapshot_id)
        except Exception:
            pass

        return SourceCoverage(
            table_name=table_label,
            row_count=row_count,
            earliest_timestamp=earliest,
            latest_timestamp=latest,
            snapshot_id=snap_id,
        )

    def list_symbols(
        self,
        *,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
        sources: Optional[list[str]] = None,
    ) -> AdjustedSymbolsResponse:
        """Distinct tickers present in the v2 adjusted-OHLCV sources
        within the time window (CV28).

        `sources` (default both) accepts a subset of
        ['polygon_adjusted', 'schwab_universe']; pass one for "what's
        in this source only", omit for the UNION.

        `since` defaults to 30 days back if None — same convention as
        the v1 /lake/symbols endpoint. Bounded scan is required;
        unbounded distinct over polygon_adjusted reads ~5y × 32
        buckets × 60 months of partition metadata.

        `limit` truncates the SORTED (alphabetical) list. None = no
        cap. The sort happens BEFORE truncation so re-querying with
        a smaller limit returns the same prefix.

        Cost: one distinct-symbol scan per requested source. Symbol
        column-only projection + timestamp-window filter. Sub-second
        on warm cache for a 30d window.
        """
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=30)
        since_utc = _coerce_utc(since)

        wanted = list(sources) if sources else [
            "polygon_adjusted", "schwab_universe",
        ]
        sources_scanned: list[str] = []
        symbol_set: set[str] = set()

        for name in wanted:
            if name == "polygon_adjusted":
                getter = self._get_ohlcv_table
                fq = "equities.polygon_adjusted"
            elif name == "schwab_universe":
                getter = self._get_schwab_table
                fq = "equities.schwab_universe"
            else:
                logger.warning(
                    "AdjustedOhlcvReader.list_symbols: unknown source "
                    "%r; skipping", name,
                )
                continue
            sources_scanned.append(fq)

            try:
                table = getter()
            except Exception as e:
                logger.warning(
                    "AdjustedOhlcvReader.list_symbols: %s not loadable "
                    "(%s); skipping this source", fq, e,
                )
                continue

            try:
                arrow = table.scan(
                    row_filter=GreaterThanOrEqual("timestamp", since_utc),
                    selected_fields=("symbol",),
                ).to_arrow()
            except Exception as e:
                logger.warning(
                    "AdjustedOhlcvReader.list_symbols: %s scan failed "
                    "(%s); skipping this source", fq, e,
                )
                continue

            if arrow.num_rows == 0:
                continue
            for s in arrow.column("symbol").to_pylist():
                if s is None:
                    continue
                symbol_set.add(str(s))

        symbols = sorted(symbol_set)
        if limit is not None and limit > 0:
            symbols = symbols[:limit]

        return AdjustedSymbolsResponse(
            sources_scanned=sources_scanned,
            since=since_utc,
            symbols=symbols,
            count=len(symbols),
        )

    def get_cross_provider_diff(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        tolerance: float = 0.005,
    ) -> CrossProviderDiffResponse:
        """Surface (symbol, timestamp) close-price disagreements
        between polygon_adjusted and schwab_universe in `[start, end)`
        whose abs(pct_diff) > `tolerance` (CV27).

        Algorithm:
          1. Window-scan both tables (single-symbol, bucket-pruned).
          2. Inner-join in Python on (symbol, timestamp) — only
             rows present in BOTH sources can be compared.
          3. Compute pct_diff = (polygon.close - schwab.close) / polygon.close.
          4. Filter to abs(pct_diff) > tolerance.
          5. Sort by timestamp ASC for deterministic output.

        `compared_count` reports the size of the inner-join (the
        denominator). `count` reports len(disagreements). If
        `compared_count > 0 and count == 0`, the sources agree across
        the whole window — that's a useful "no bugs here" signal.

        Single-sided rows (only in polygon OR only in schwab) are NOT
        surfaced; coverage gaps are a different question, answered by
        `get_symbol_coverage()`.

        Tolerance defaults to 0.005 (50bps) — picked to filter out
        normal sub-cent rounding noise while surfacing the real
        corp-action / data-correction divergences agents care about.

        Cost: two single-symbol window scans (each ~1/32 of relevant
        month metadata thanks to CV1's bucketing). For a 30-day
        window on AAPL that's ~30K rows per source merged in Python —
        sub-second on warm cache.
        """
        sym = (symbol or "").strip().upper()
        start_utc = _coerce_utc(start)
        end_utc = _coerce_utc(end)
        if not sym:
            return CrossProviderDiffResponse(
                symbol=symbol or "",
                start=start_utc, end=end_utc,
                tolerance=tolerance,
                compared_count=0,
                disagreements=[],
                count=0,
            )

        polygon_arrow = self._scan_window(
            self._get_ohlcv_table, sym, start_utc, end_utc,
            label="equities.polygon_adjusted",
        )
        schwab_arrow = self._scan_window(
            self._get_schwab_table, sym, start_utc, end_utc,
            label="equities.schwab_universe",
        )

        # Build {timestamp → close} maps for the inner-join.
        polygon_closes: dict = {}
        if polygon_arrow is not None and polygon_arrow.num_rows > 0:
            polygon_closes = self._closes_by_ts(polygon_arrow)
        schwab_closes: dict = {}
        if schwab_arrow is not None and schwab_arrow.num_rows > 0:
            schwab_closes = self._closes_by_ts(schwab_arrow)

        common_ts = sorted(polygon_closes.keys() & schwab_closes.keys())

        disagreements: list[CrossProviderDiffRow] = []
        for ts in common_ts:
            p = polygon_closes[ts]
            s = schwab_closes[ts]
            if p is None or s is None:
                continue
            abs_diff = abs(p - s)
            pct_diff = (p - s) / p if p != 0 else 0.0
            if abs(pct_diff) > tolerance:
                disagreements.append(
                    CrossProviderDiffRow(
                        timestamp=ts,
                        polygon_close=p,
                        schwab_close=s,
                        abs_diff=abs_diff,
                        pct_diff=pct_diff,
                    )
                )

        return CrossProviderDiffResponse(
            symbol=sym,
            start=start_utc,
            end=end_utc,
            tolerance=tolerance,
            compared_count=len(common_ts),
            disagreements=disagreements,
            count=len(disagreements),
        )

    @staticmethod
    def _closes_by_ts(arrow) -> dict:
        """Build {tz-aware ts → close} from an Arrow Table. Drops
        rows with NULL close (can't compare)."""
        if arrow.num_rows == 0:
            return {}
        ts_col = arrow.column("timestamp").to_pylist()
        close_col = arrow.column("close").to_pylist()
        out: dict = {}
        for ts, c in zip(ts_col, close_col):
            if ts is None or c is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out[ts] = float(c)
        return out

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
                "AdjustedOhlcvReader: bar_quality table fixture failed (%s); "
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
                "AdjustedOhlcvReader: bar_quality scan failed for %s: %s",
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
                    "AdjustedOhlcvReader: skipping row with NULL OHLC for "
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
