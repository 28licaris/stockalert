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
from pyiceberg.expressions import (
    And,
    EqualTo,
    GreaterThan,
    GreaterThanOrEqual,
    LessThan,
)

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

    def compute_slice(
        self,
        symbol: str,
        day: date,
        *,
        run_id: Optional[str] = None,
    ) -> tuple[SliceResult, Optional[pa.Table], Optional[pa.Table]]:
        """The READ + NORMALIZE + MERGE half of build_slice — no writes.

        Returns (SliceResult, ohlcv_arrow_or_None, bar_quality_arrow_or_None).
        The arrows are None when there's no data to write (cold-start
        symbol, weekend day, etc.).

        This split lets `build_window_concurrent` run the I/O-bound +
        CPU-bound compute work in parallel (many slices at once via
        asyncio.Semaphore + asyncio.to_thread) while batching the
        PyIceberg upserts per-day in serial — avoiding optimistic-
        concurrency commit conflicts.

        Use `build_slice` for the simple sequential path (compute +
        write in one call).
        """
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

                # 2. Normalize this provider's rows into the canonical
                #    split-adjusted frame.
                normalized = normalize_provider_rows(
                    rows,
                    adjustment_status=routing.adjustment_status,
                    split_index=self._get_split_index(),
                )
                per_provider_rows.append((provider, normalized))

            if not per_provider_rows:
                # No bronze data for this slice at all. Not an error —
                # the symbol may not have traded this day.
                return result, None, None

            # 3. Merge with precedence + compute bar_quality.
            ohlcv_arrow = merge_with_precedence(per_provider_rows, run_id=run_id)
            quality_arrow = compute_bar_quality(per_provider_rows, run_id=run_id)
            return result, ohlcv_arrow, quality_arrow

        except Exception as e:
            logger.exception(
                "silver_ohlcv_build: slice (%s, %s) compute failed: %s",
                symbol, day, e,
            )
            result.error = f"{type(e).__name__}: {e}"
            return result, None, None

    def build_slice(
        self,
        symbol: str,
        day: date,
        *,
        run_id: Optional[str] = None,
    ) -> SliceResult:
        """Build one (symbol, day) slice end-to-end (compute + write).

        Sequential convenience wrapper around compute_slice + the two
        upserts. For high-volume backfills, prefer
        `build_window_concurrent` which batches upserts per-day.
        """
        result, ohlcv_arrow, quality_arrow = self.compute_slice(
            symbol, day, run_id=run_id,
        )
        if not result.succeeded:
            return result

        try:
            if ohlcv_arrow is not None and ohlcv_arrow.num_rows > 0:
                self._get_ohlcv_table().upsert(ohlcv_arrow)
                result.silver_rows_written = ohlcv_arrow.num_rows
            if quality_arrow is not None and quality_arrow.num_rows > 0:
                self._get_bar_quality_table().upsert(quality_arrow)
                result.quality_row_written = True
        except Exception as e:
            logger.exception(
                "silver_ohlcv_build: slice (%s, %s) upsert failed: %s",
                symbol, day, e,
            )
            result.error = f"{type(e).__name__}: {e}"

        return result

    def build_window(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        *,
        max_concurrency: int = 1,
    ) -> BuildResult:
        """Build all (symbol, day) slices in the window.

        When `max_concurrency > 1`, dispatches to the concurrent
        implementation which:
          - runs `compute_slice` for many slices in parallel via
            `asyncio.Semaphore(max_concurrency)` + `asyncio.to_thread`
            (the I/O-bound + CPU-bound work that's 95% of wall-clock)
          - batches the PyIceberg upserts per-day (one upsert per
            table per day) — keeps optimistic-concurrency commit
            churn low

        `max_concurrency=1` (default) preserves the original sequential
        behavior: opt-in to parallelism for safety.

        Either way: idempotent (re-running same window upserts identical
        rows modulo ingestion_ts), per-slice error-isolated (one bad
        symbol doesn't abort the rest).
        """
        if max_concurrency > 1:
            return self._build_window_concurrent(
                symbols, start_date, end_date, max_concurrency=max_concurrency,
            )

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
            "silver_ohlcv_build: starting run_id=%s symbols=%d window=%s..%s "
            "concurrency=1 (sequential)",
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

    def run_nightly(
        self,
        symbols: Optional[Iterable[str]] = None,
        *,
        scan_corp_action_dirty: bool = True,
    ) -> BuildResult:
        """Yesterday's slice for the active universe, plus any dirty
        slices that need rebuilding because corp-actions changed.

        Default symbols = `get_active_universe()` (SEED_SYMBOLS ∪
        active-watchlist symbols) per G1 dynamic universe. Pass an
        explicit list to override (operator override / one-shot rebuilds).

        **Corp-action dirty rebuild (TA-5.1.9):** when set
        (`scan_corp_action_dirty=True`, default), the run first queries
        silver.corp_actions for splits ingested since the last
        successful silver_ohlcv_build run. For each affected symbol it
        rebuilds the full history window (bars before the new ex_date)
        because the cumulative split factor F changed. Without this,
        new splits would create silent price discontinuities at the
        ex_date in historical silver data. See
        `find_corp_action_dirty_symbols` for the scan logic.
        """
        if symbols is None:
            from app.services.universe import get_active_universe
            symbols = get_active_universe()
        else:
            symbols = list(symbols)
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)

        # Phase 1: corp-action dirty rebuilds (if enabled).
        # Run BEFORE the normal yesterday-window so that any rebuild
        # picks up the same corp_actions state the yesterday build will use.
        if scan_corp_action_dirty:
            dirty_result = self._run_corp_action_dirty_rebuilds()
            # If we got any dirty rebuilds, return the combined result.
            # If not, fall through to a pure yesterday-only build.
            if dirty_result is not None and dirty_result.slices:
                # Run yesterday's slice and merge into the dirty result.
                yesterday_result = self.build_window(symbols, yesterday, yesterday)
                dirty_result.slices.extend(yesterday_result.slices)
                dirty_result.finished_at = yesterday_result.finished_at
                return dirty_result

        # Phase 2: normal yesterday × universe.
        return self.build_window(symbols, yesterday, yesterday)

    def run_full(
        self,
        *,
        symbols: Optional[Iterable[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        max_concurrency: int = 1,
    ) -> BuildResult:
        """Build the full silver history from bronze.

        Defaults: symbols = `get_active_universe()` per G1; start =
        2021-01-04 (bronze polygon coverage start); end = yesterday.
        Wall-clock measured in hours for a full rebuild at concurrency=1
        — operator script intended. Set `max_concurrency=8` (or higher)
        to parallelize; ~5-8x speedup typical.
        """
        if symbols is None:
            from app.services.universe import get_active_universe
            symbols = get_active_universe()
        else:
            symbols = list(symbols)
        start = start_date or date(2021, 1, 4)
        end = end_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
        return self.build_window(
            symbols, start, end, max_concurrency=max_concurrency,
        )

    # ─────────────────────────────────────────────────────────────────
    # Concurrent build path (TA-5.1.10)
    # ─────────────────────────────────────────────────────────────────
    #
    # Each compute_slice is independent and I/O-bound (S3 latency
    # dominates). N-way concurrency across (symbol, day) pairs gives
    # ~Nx speedup up to PyIceberg upsert commit-churn limits.
    #
    # Per-day batched upserts: we collect all per-day Arrow tables in
    # memory and do ONE upsert per silver table per day. PyIceberg's
    # optimistic concurrency means concurrent upserts to the same
    # table retry on conflict — batching to per-day amortizes commits
    # and avoids retry storms.
    #
    # Sweet spot is ~8 concurrent slices (per the speedup options doc).
    # Higher concurrency hits diminishing returns from S3 rate limits
    # and CPU contention on PyArrow merges.

    def _build_window_concurrent(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        *,
        max_concurrency: int,
    ) -> BuildResult:
        """Concurrent version of build_window. See build_window's
        docstring for the high-level contract."""
        import asyncio

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
            "silver_ohlcv_build: starting run_id=%s symbols=%d window=%s..%s "
            "concurrency=%d",
            run_id, len(symbols), start_date, end_date, max_concurrency,
        )

        # Prime corp-actions cache once before fan-out.
        self._prime_corp_actions_cache()

        # Iterate day-by-day. For each day, fan out the compute work for
        # all symbols, then do ONE batched upsert per silver table.
        # Day-by-day keeps partial-failure recovery clean (a crash mid-
        # window loses at most one day of work).
        current = start_date
        sem = asyncio.Semaphore(max_concurrency)

        async def _compute_one(symbol: str, day: date):
            async with sem:
                return await asyncio.to_thread(
                    self.compute_slice, symbol, day, run_id=run_id,
                )

        async def _run_day(day: date) -> list[SliceResult]:
            tasks = [_compute_one(sym, day) for sym in symbols]
            outputs = await asyncio.gather(*tasks)

            # Batch all this day's Arrow tables into per-table upserts.
            ohlcv_batch: list[pa.Table] = []
            quality_batch: list[pa.Table] = []
            slice_results: list[SliceResult] = []
            for sr, ohlcv_arrow, quality_arrow in outputs:
                slice_results.append(sr)
                if (
                    sr.succeeded
                    and ohlcv_arrow is not None
                    and ohlcv_arrow.num_rows > 0
                ):
                    ohlcv_batch.append(ohlcv_arrow)
                if (
                    sr.succeeded
                    and quality_arrow is not None
                    and quality_arrow.num_rows > 0
                ):
                    quality_batch.append(quality_arrow)

            # One upsert per table per day.
            if ohlcv_batch:
                combined = pa.concat_tables(ohlcv_batch)
                try:
                    self._get_ohlcv_table().upsert(combined)
                    # Re-attribute row counts to each contributing slice
                    # so SliceResult reflects what got written.
                    for sr, ohlcv_arrow, _ in outputs:
                        if (
                            sr.succeeded
                            and ohlcv_arrow is not None
                        ):
                            sr.silver_rows_written = ohlcv_arrow.num_rows
                except Exception as e:
                    logger.exception(
                        "silver_ohlcv_build: day=%s ohlcv batch upsert "
                        "failed: %s", day, e,
                    )
                    err = f"{type(e).__name__}: {e}"
                    for sr in slice_results:
                        if sr.succeeded:
                            sr.error = err

            if quality_batch:
                combined_q = pa.concat_tables(quality_batch)
                try:
                    self._get_bar_quality_table().upsert(combined_q)
                    for sr, _, quality_arrow in outputs:
                        if (
                            sr.succeeded
                            and quality_arrow is not None
                        ):
                            sr.quality_row_written = True
                except Exception as e:
                    logger.exception(
                        "silver_ohlcv_build: day=%s bar_quality batch "
                        "upsert failed: %s", day, e,
                    )
                    # bar_quality failure is a soft error — log but
                    # don't mark the whole slice failed (ohlcv is the
                    # canonical data).

            return slice_results

        async def _run_all() -> None:
            nonlocal current
            while current <= end_date:
                day_results = await _run_day(current)
                result.slices.extend(day_results)
                current += timedelta(days=1)

        try:
            asyncio.run(_run_all())
        except RuntimeError as e:
            # If called from inside an existing event loop (unusual —
            # this method is typically called from a sync CLI), the
            # asyncio.run() call raises. Fall back to a loop.
            if "running event loop" in str(e):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run_all())
                finally:
                    loop.close()
            else:
                raise

        result.finished_at = datetime.now(timezone.utc)
        logger.info(
            "silver_ohlcv_build: done run_id=%s slices=%d (ok=%d fail=%d) "
            "silver_rows=%d duration=%.1fs concurrency=%d",
            run_id, len(result.slices), result.slices_succeeded,
            result.slices_failed, result.total_silver_rows,
            result.duration_seconds, max_concurrency,
        )

        # Record one run row in ingestion_runs (best-effort).
        self._record_run(result)

        # Clear caches so next run reloads fresh.
        self._split_index = None
        self._corp_actions_arrow = None

        return result

    # ─────────────────────────────────────────────────────────────────
    # Corp-action rebuild trigger (TA-5.1.9)
    # ─────────────────────────────────────────────────────────────────
    #
    # When a new split lands in silver.corp_actions for symbol S with
    # ex_date X, every silver.ohlcv_1m row for S with bar_date < X has
    # a stale F (cumulative split factor) baked in. Those rows need
    # recompute or the chart shows a discontinuity at X.
    #
    # The scan compares silver.corp_actions's `ingestion_ts` against
    # the previous successful silver_ohlcv_build run's `started_at`
    # (read from CH `ingestion_runs`). Anything newer = dirty.
    #
    # Rebuild window per affected symbol:
    #   start = BRONZE_HISTORY_START (2021-01-04)
    #   end   = max(new ex_dates for that symbol) - 1 day
    # We rebuild the FULL history before the latest new ex_date because
    # multiple back-dated splits could chain (rare but possible).

    BRONZE_HISTORY_START = date(2021, 1, 4)

    def find_corp_action_dirty_symbols(
        self, since: datetime,
    ) -> dict[str, date]:
        """Find symbols whose silver history is stale due to new splits.

        Returns `{symbol: max_new_ex_date}` — the rebuild window for
        each symbol is `(BRONZE_HISTORY_START, max_new_ex_date - 1)`.
        Empty dict if no new splits since `since`.

        `since` is the prior successful silver_ohlcv_build run's
        `started_at` timestamp (UTC). Anything in silver.corp_actions
        with `ingestion_ts > since AND action_type = 'split'` is new.
        """
        try:
            ca_table = self._get_catalog().load_table(
                silver_table_id("corp_actions"),
            )
        except NoSuchTableError:
            logger.info(
                "find_corp_action_dirty_symbols: silver.corp_actions "
                "absent; no dirty symbols",
            )
            return {}

        try:
            arrow = ca_table.scan(
                row_filter=And(
                    EqualTo("action_type", "split"),
                    GreaterThan("ingestion_ts", since),
                ),
                selected_fields=("symbol", "ex_date", "ingestion_ts"),
            ).to_arrow()
        except Exception as e:
            logger.warning(
                "find_corp_action_dirty_symbols: corp_actions scan "
                "failed: %s; returning empty (no rebuild this pass)", e,
            )
            return {}

        if arrow.num_rows == 0:
            return {}

        per_symbol: dict[str, date] = {}
        for row in arrow.to_pylist():
            sym = row.get("symbol")
            ex = row.get("ex_date")
            if not sym or not ex:
                continue
            prev = per_symbol.get(sym)
            if prev is None or ex > prev:
                per_symbol[sym] = ex
        return per_symbol

    def _get_last_run_started_at(self) -> Optional[datetime]:
        """Read the latest successful silver_ohlcv_build run's
        `started_at` from CH ingestion_runs. Returns None if none
        recorded (cold start)."""
        try:
            from app.db import get_client

            client = get_client()
            result = client.query(
                """
                SELECT max(started_at)
                FROM ingestion_runs
                WHERE job_name = 'silver_ohlcv_build'
                  AND status IN ('ok', 'partial_fail')
                """,
            )
            if not result.result_rows:
                return None
            ts = result.result_rows[0][0]
            if ts is None:
                return None
            # CH returns naive datetime in UTC; upgrade to tz-aware.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except Exception as e:
            logger.warning(
                "_get_last_run_started_at: CH read failed (%s); "
                "returning None — corp-action dirty scan will use a "
                "default lookback instead", e,
            )
            return None

    def _run_corp_action_dirty_rebuilds(self) -> Optional[BuildResult]:
        """Scan for dirty symbols + rebuild affected windows.

        Returns the BuildResult of the rebuild pass, or None if
        nothing was dirty (so the caller can skip merging).
        """
        # Watermark for the scan: prior successful run's started_at.
        # If none recorded (cold start), default to "7 days ago" so the
        # first auto-run after silver-build go-live picks up any splits
        # that landed during the initial backfill window.
        since = self._get_last_run_started_at()
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=7)
            logger.info(
                "_run_corp_action_dirty_rebuilds: no prior run watermark; "
                "scanning corp_actions ingested since %s (default 7d lookback)",
                since,
            )

        dirty = self.find_corp_action_dirty_symbols(since)
        if not dirty:
            logger.info(
                "_run_corp_action_dirty_rebuilds: no dirty symbols "
                "(no new splits since %s)", since,
            )
            return None

        logger.info(
            "_run_corp_action_dirty_rebuilds: %d symbols dirty: %s",
            len(dirty), sorted(dirty.keys()),
        )

        # Build a separate run per affected symbol because each has its
        # own end date. Could be batched into one run_id for audit
        # cleanliness — do that here, building one window per symbol
        # but accumulating slice results into a single BuildResult.
        run_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc)
        combined = BuildResult(
            run_id=run_id,
            started_at=started,
            finished_at=started,
            symbols=sorted(dirty.keys()),
            start_date=self.BRONZE_HISTORY_START,
            end_date=max(dirty.values()),
        )

        # Prime corp-actions cache once for the whole rebuild pass.
        self._prime_corp_actions_cache()

        for symbol, max_ex_date in dirty.items():
            # Rebuild window: everything strictly before the new ex_date.
            # The new ex_date's own bars + going forward already have
            # the correct F (their ingestion already saw the new split).
            window_end = max_ex_date - timedelta(days=1)
            window_start = self.BRONZE_HISTORY_START
            if window_end < window_start:
                continue
            logger.info(
                "corp-action-dirty rebuild: %s %s..%s",
                symbol, window_start, window_end,
            )
            current = window_start
            while current <= window_end:
                slice_result = self.build_slice(symbol, current, run_id=run_id)
                combined.slices.append(slice_result)
                current += timedelta(days=1)

        combined.finished_at = datetime.now(timezone.utc)
        logger.info(
            "_run_corp_action_dirty_rebuilds: done run_id=%s symbols=%d "
            "slices=%d (ok=%d fail=%d) silver_rows=%d duration=%.1fs",
            run_id, len(dirty), len(combined.slices),
            combined.slices_succeeded, combined.slices_failed,
            combined.total_silver_rows, combined.duration_seconds,
        )

        # Clear caches (run_nightly's normal phase will re-prime).
        self._split_index = None
        self._corp_actions_arrow = None

        # Record the run for audit.
        self._record_run(combined)

        return combined

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
