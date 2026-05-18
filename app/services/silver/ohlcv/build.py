"""
Silver OHLCV build orchestrator.

Wires the four pipeline pieces (bronze read → normalize → merge →
bar_quality) into a complete build job that produces
`silver.ohlcv_1m` + `silver.bar_quality` rows.

Three execution modes (all in-process; no external cron):
  - `build_slice(symbol, day)` — process one (symbol, day) slice
  - `build_window(symbols, start_date, end_date)` — process a range
  - `run_nightly()` — yesterday's slice for the active universe
  - `run_full(symbols, start_date, end_date)` — initial backfill /
    catchup. The operator CLI surface (TA-5.1.6 next).

Idempotent: re-running a slice produces byte-identical silver rows
(modulo `ingestion_ts` and `ingestion_run_id`). PyIceberg's upsert
on the identifier `(symbol, timestamp)` for ohlcv_1m and
`(symbol, date)` for bar_quality handles re-write cleanly.

Per [silver_layer_plan §3](../../../../docs/silver_layer_plan.md).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import pyarrow as pa
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

from app.config import settings
from app.services.bronze.schemas import (
    BRONZE_POLYGON_MINUTE_ADJUSTMENT_STATUS,
    BRONZE_SCHWAB_MINUTE_ADJUSTMENT_STATUS,
    bronze_table_id,
)
from app.services.iceberg_catalog import get_catalog
from app.services.silver.ohlcv.merge import (
    compute_bar_quality,
    merge_with_precedence,
)
from app.services.silver.ohlcv.normalize import (
    SplitFactors,
    build_split_factor_index,
    normalize_provider_rows,
)
from app.services.silver.schemas import silver_table_id
from app.services.silver.tables import (
    ensure_silver_bar_quality,
    ensure_silver_ohlcv_1m,
)

logger = logging.getLogger(__name__)


# Per-provider routing for the build:
#   provider_name → (bronze_table_short, adjustment_status_constant)
#
# Same provider-pluggable pattern as the corp-actions build: adding a
# new provider = one entry here + entries in the bronze schemas. ZERO
# orchestrator changes.
@dataclass(frozen=True)
class _ProviderRouting:
    bronze_short: str
    adjustment_status: str


_PROVIDER_ROUTING: dict[str, _ProviderRouting] = {
    "polygon": _ProviderRouting(
        bronze_short="polygon_minute",
        adjustment_status=BRONZE_POLYGON_MINUTE_ADJUSTMENT_STATUS,
    ),
    "schwab": _ProviderRouting(
        bronze_short="schwab_minute",
        adjustment_status=BRONZE_SCHWAB_MINUTE_ADJUSTMENT_STATUS,
    ),
}


# ─────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SliceResult:
    """Result of one `build_slice(symbol, day)` call."""
    symbol: str
    date: date
    polygon_rows_read: int = 0
    schwab_rows_read: int = 0
    silver_rows_written: int = 0
    quality_row_written: bool = False
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class BuildResult:
    """Result of a build_window / run_nightly / run_full call."""
    run_id: str
    started_at: datetime
    finished_at: datetime
    symbols: list[str] = field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    slices: list[SliceResult] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def total_silver_rows(self) -> int:
        return sum(s.silver_rows_written for s in self.slices)

    @property
    def slices_failed(self) -> int:
        return sum(1 for s in self.slices if not s.succeeded)

    @property
    def slices_succeeded(self) -> int:
        return sum(1 for s in self.slices if s.succeeded)


# ─────────────────────────────────────────────────────────────────────
# The orchestrator
# ─────────────────────────────────────────────────────────────────────


class SilverOhlcvBuild:
    """Bronze → silver.ohlcv_1m + silver.bar_quality build job.

    Construct via `from_settings()` for production; pass explicit
    `provider_precedence` / `catalog` / pre-loaded tables for tests.
    """

    def __init__(
        self,
        *,
        catalog=None,
        ohlcv_table=None,
        bar_quality_table=None,
        provider_precedence: Optional[list[str]] = None,
    ) -> None:
        self._catalog = catalog
        self._ohlcv_table = ohlcv_table
        self._bar_quality_table = bar_quality_table
        self._provider_precedence = provider_precedence

        # Caches built once per run; cleared between runs.
        self._split_index: Optional[SplitFactors] = None
        self._corp_actions_arrow: Optional[pa.Table] = None

    @classmethod
    def from_settings(cls) -> "SilverOhlcvBuild":
        precedence = [
            p.strip()
            for p in (settings.silver_provider_precedence or "").split(",")
            if p.strip()
        ]
        if not precedence:
            raise ValueError(
                "silver_provider_precedence is empty. Set SILVER_PROVIDER_PRECEDENCE."
            )
        return cls(provider_precedence=precedence)

    # ─────────────────────────────────────────────────────────────────
    # Public modes
    # ─────────────────────────────────────────────────────────────────

    def build_slice(
        self,
        symbol: str,
        day: date,
        *,
        run_id: Optional[str] = None,
    ) -> SliceResult:
        """Build one (symbol, day) slice end-to-end."""
        run_id = run_id or uuid.uuid4().hex
        result = SliceResult(symbol=symbol, date=day)
        try:
            day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)

            per_provider_rows: list[tuple[str, list[dict]]] = []

            # 1. Read each provider's bronze for the slice, in precedence
            #    order so the merge sees the right priority.
            for provider in self._get_precedence():
                routing = _PROVIDER_ROUTING.get(provider)
                if routing is None:
                    logger.warning(
                        "silver_ohlcv_build: provider %r in precedence list "
                        "but no routing config; skipping", provider,
                    )
                    continue
                rows = self._read_bronze_slice(
                    routing.bronze_short, symbol, day_start, day_end,
                )
                if provider == "polygon":
                    result.polygon_rows_read = len(rows)
                elif provider == "schwab":
                    result.schwab_rows_read = len(rows)

                if not rows:
                    continue

                # 2. Normalize this provider's rows to BOTH _raw and _adj.
                normalized = normalize_provider_rows(
                    rows,
                    adjustment_status=routing.adjustment_status,
                    split_index=self._get_split_index(),
                )
                per_provider_rows.append((provider, normalized))

            if not per_provider_rows:
                # No bronze data for this slice at all. Not an error —
                # the symbol may not have traded this day.
                return result

            # 3. Merge with precedence + compute bar_quality.
            ohlcv_arrow = merge_with_precedence(per_provider_rows, run_id=run_id)
            quality_arrow = compute_bar_quality(per_provider_rows, run_id=run_id)

            # 4. Upsert into silver tables.
            if ohlcv_arrow.num_rows > 0:
                self._get_ohlcv_table().upsert(ohlcv_arrow)
                result.silver_rows_written = ohlcv_arrow.num_rows
            if quality_arrow.num_rows > 0:
                self._get_bar_quality_table().upsert(quality_arrow)
                result.quality_row_written = True

        except Exception as e:
            logger.exception(
                "silver_ohlcv_build: slice (%s, %s) failed: %s", symbol, day, e,
            )
            result.error = f"{type(e).__name__}: {e}"

        return result

    def build_window(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> BuildResult:
        """Build all (symbol, day) slices in the window.

        Iterates day-by-day across each symbol. Single-process today;
        future enhancement could parallelize per-symbol with a
        semaphore.
        """
        run_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc)
        result = BuildResult(
            run_id=run_id,
            started_at=started,
            finished_at=started,
            symbols=list(symbols),
            start_date=start_date,
            end_date=end_date,
        )

        logger.info(
            "silver_ohlcv_build: starting run_id=%s symbols=%d window=%s..%s",
            run_id, len(symbols), start_date, end_date,
        )

        # Prime the corp-actions cache + split index for the run.
        # Reading once + caching saves N×slices reads.
        self._prime_corp_actions_cache()

        # Iterate day-by-day so partial-failure recovery is per-day,
        # not per-symbol-spanning-years.
        current = start_date
        while current <= end_date:
            for symbol in symbols:
                slice_result = self.build_slice(symbol, current, run_id=run_id)
                result.slices.append(slice_result)
            current += timedelta(days=1)

        result.finished_at = datetime.now(timezone.utc)
        logger.info(
            "silver_ohlcv_build: done run_id=%s slices=%d (ok=%d fail=%d) "
            "silver_rows=%d duration=%.1fs",
            run_id, len(result.slices), result.slices_succeeded,
            result.slices_failed, result.total_silver_rows,
            result.duration_seconds,
        )

        # Record one run row in ingestion_runs (best-effort).
        self._record_run(result)

        # Clear caches so next run reloads fresh.
        self._split_index = None
        self._corp_actions_arrow = None

        return result

    def run_nightly(self, symbols: Optional[Iterable[str]] = None) -> BuildResult:
        """Yesterday's slice for the active universe.

        Default symbols = SEED_SYMBOLS for now. After G1 lands (dynamic
        universe), this will use `get_active_universe()`.
        """
        if symbols is None:
            from app.data.seed_universe import SEED_SYMBOLS
            symbols = list(SEED_SYMBOLS)
        else:
            symbols = list(symbols)
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        return self.build_window(symbols, yesterday, yesterday)

    def run_full(
        self,
        *,
        symbols: Optional[Iterable[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> BuildResult:
        """Build the full silver history from bronze.

        Defaults: symbols = SEED_SYMBOLS; start = 2021-01-04 (bronze
        polygon coverage start); end = yesterday. Wall-clock measured
        in hours for a full rebuild — operator script intended.
        """
        if symbols is None:
            from app.data.seed_universe import SEED_SYMBOLS
            symbols = list(SEED_SYMBOLS)
        else:
            symbols = list(symbols)
        start = start_date or date(2021, 1, 4)
        end = end_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
        return self.build_window(symbols, start, end)

    # ─────────────────────────────────────────────────────────────────
    # Per-pipeline-step helpers
    # ─────────────────────────────────────────────────────────────────

    def _get_precedence(self) -> list[str]:
        if self._provider_precedence is None:
            self._provider_precedence = [
                p.strip()
                for p in (settings.silver_provider_precedence or "").split(",")
                if p.strip()
            ]
        return self._provider_precedence

    def _get_catalog(self):
        if self._catalog is None:
            self._catalog = get_catalog()
        return self._catalog

    def _get_ohlcv_table(self):
        if self._ohlcv_table is None:
            self._ohlcv_table = ensure_silver_ohlcv_1m(self._get_catalog())
        return self._ohlcv_table

    def _get_bar_quality_table(self):
        if self._bar_quality_table is None:
            self._bar_quality_table = ensure_silver_bar_quality(self._get_catalog())
        return self._bar_quality_table

    def _read_bronze_slice(
        self,
        bronze_short: str,
        symbol: str,
        day_start: datetime,
        day_end: datetime,
    ) -> list[dict]:
        """Read bronze.{short} for (symbol, [day_start, day_end))."""
        try:
            table = self._get_catalog().load_table(bronze_table_id(bronze_short))
        except NoSuchTableError:
            return []

        scan = table.scan(
            row_filter=And(
                EqualTo("symbol", symbol),
                GreaterThanOrEqual("timestamp", day_start),
                LessThan("timestamp", day_end),
            ),
            selected_fields=(
                "symbol", "timestamp",
                "open", "high", "low", "close", "volume",
                "vwap", "trade_count", "source",
            ),
        )
        try:
            arrow = scan.to_arrow()
        except Exception as e:
            logger.warning(
                "silver_ohlcv_build: scan failed for %s %s: %s",
                bronze_short, symbol, e,
            )
            return []
        return arrow.to_pylist() if arrow.num_rows > 0 else []

    def _prime_corp_actions_cache(self) -> None:
        """Load silver.corp_actions once → build the split-factor index.

        Cached for the duration of one build run. Cleared in
        build_window's finally block.
        """
        if self._corp_actions_arrow is not None:
            return  # already primed
        try:
            ca_table_id = silver_table_id("corp_actions")
            ca_table = self._get_catalog().load_table(ca_table_id)
            # Pull only the columns the split index needs.
            arrow = ca_table.scan(
                selected_fields=("symbol", "ex_date", "action_type", "factor"),
            ).to_arrow()
            self._corp_actions_arrow = arrow
            self._split_index = build_split_factor_index(arrow)
            logger.info(
                "silver_ohlcv_build: loaded %d corp_actions rows; "
                "%d symbols have splits",
                arrow.num_rows, len(self._split_index),
            )
        except NoSuchTableError:
            # silver.corp_actions doesn't exist yet — F = 1 for every symbol.
            logger.warning(
                "silver_ohlcv_build: silver.corp_actions not present; "
                "running with empty split index (no adjustment applied)",
            )
            self._corp_actions_arrow = None
            self._split_index = {}
        except Exception as e:
            logger.warning(
                "silver_ohlcv_build: failed to load corp_actions: %s; "
                "running with empty split index", e,
            )
            self._split_index = {}

    def _get_split_index(self) -> SplitFactors:
        if self._split_index is None:
            self._prime_corp_actions_cache()
        return self._split_index or {}

    def _record_run(self, result: BuildResult) -> None:
        """Best-effort: append to CH `ingestion_runs` for audit."""
        try:
            from app.db import get_client

            client = get_client()
            window = (
                f"{result.start_date}..{result.end_date}"
                if result.start_date else ""
            )
            client.insert(
                "ingestion_runs",
                [[
                    result.run_id,
                    "silver_ohlcv_build",
                    result.started_at,
                    result.finished_at,
                    (
                        datetime.combine(result.start_date, datetime.min.time(),
                                         tzinfo=timezone.utc)
                        if result.start_date else datetime.now(timezone.utc)
                    ),
                    (
                        datetime.combine(result.end_date, datetime.min.time(),
                                         tzinfo=timezone.utc)
                        if result.end_date else datetime.now(timezone.utc)
                    ),
                    result.total_silver_rows,
                    f'{{"symbols": {len(result.symbols)}, "slices": {len(result.slices)}, '
                    f'"succeeded": {result.slices_succeeded}, "failed": {result.slices_failed}}}',
                    "",
                    "ok" if result.slices_failed == 0 else "partial_fail",
                ]],
                column_names=[
                    "run_id", "job_name", "started_at", "finished_at",
                    "window_start", "window_end", "rows_written",
                    "per_provider_rows_written_json",
                    "per_provider_errors_json",
                    "status",
                ],
            )
        except Exception as e:
            logger.debug(
                "silver_ohlcv_build: ingestion_runs recording failed (best-effort): %s",
                e,
            )
