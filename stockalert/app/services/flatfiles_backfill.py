"""
Bulk historical backfill from Polygon Flat Files.

The contract is intentionally narrow:

    "Given a symbol universe and a date range, download Polygon's
    flat-file aggregates for those days, canonicalise them, and fan
    them out to a configurable list of sinks."

That's it. This service does NOT:

  - Know how any individual sink stores its data (that lives in the
    sink itself: ``ClickHouseSink`` writes to ``ohlcv_1m`` /
    ``ohlcv_daily``; ``LakeSink`` writes to S3 + watermark)
  - Detect gaps. Flat files are the gold-standard *bulk* source;
    surgical intraday gap fills stay on the REST path.

Sink fan-out model
------------------
The service is constructed with ``sinks=[...]`` — a list of Sink
implementations the canonical day-frame is handed to. Sinks are:

  - **Independent**: one sink's failure does NOT stop the next
  - **Idempotent**: re-running a sink on the same day is a no-op or an
    overwrite
  - **Self-classifying**: each returns a ``SinkResult`` with its own
    ``status`` (``ok`` / ``skipped`` / ``error``); per-day status is
    aggregated from the sink results

Day status rules:
  - All sinks succeed  → ``"ok"``
  - Some succeed, some fail → ``"partial"`` (data is somewhere; lake
    verify will pick up the unfinished sink on the next pass)
  - All sinks fail → ``"error"``
  - No sinks configured → ``"skipped"`` (dry validation run)

Backward compatibility
----------------------
Pre-C2 callers passed ``insert_minute_fn`` / ``insert_daily_fn`` /
``batch_size`` and expected ClickHouse-only behaviour. We continue to
honour that: if ``sinks=None`` is passed, a single ``ClickHouseSink``
is constructed from the legacy args. ``_df_to_minute_records`` and
``_df_to_daily_records`` are kept as thin wrappers over the canonical
transform so the existing transform tests still pin behaviour.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Iterable, List, Literal, Optional, Sequence

import pandas as pd

from app.providers.polygon_flatfiles import (
    PolygonFlatFilesClient,
    PolygonFlatFilesError,
)
from app.services.flatfiles_sinks import (
    ClickHouseSink,
    InsertFn,
    Kind,
    Sink,
    SinkResult,
    _frame_to_records,
)

logger = logging.getLogger(__name__)


# ---------- result types ----------


@dataclass(slots=True)
class DayResult:
    """Outcome of one date's flat-file backfill.

    Status semantics:
      - ``"ok"``       — every configured sink wrote successfully
      - ``"partial"``  — at least one sink succeeded, at least one failed
      - ``"missing"``  — no flat-file existed for this date (weekend /
                          holiday / pre-IPO)
      - ``"filtered"`` — file present but the symbol filter eliminated
                          every row (the file *did* exist; see logs)
      - ``"error"``    — every configured sink failed, OR the download
                          itself failed
      - ``"skipped"``  — dry-run, or no sinks were configured
    """
    file_date: date
    bars_persisted: int = 0
    symbols_seen: int = 0
    status: str = "ok"
    error: Optional[str] = None
    elapsed_s: float = 0.0
    # Per-sink breakdown. Empty for the missing/filtered/error-before-sink
    # paths. ``status="partial"`` always has at least one ``ok`` and at
    # least one ``error`` entry here.
    sink_results: dict[str, SinkResult] = field(default_factory=dict)


@dataclass(slots=True)
class BackfillRangeResult:
    """Aggregated outcome of ``backfill_range``."""
    kind: Kind
    start: date
    end: date
    symbols_requested: int  # 0 means "all symbols in the file"
    days_listed: int = 0
    days_ok: int = 0
    days_missing: int = 0
    days_filtered: int = 0
    days_skipped: int = 0
    days_errored: int = 0
    # New in C2: days where at least one sink succeeded but at least one
    # failed. These do NOT count toward ``days_errored`` because the
    # data IS in at least one persistent store and a re-run will
    # idempotently complete the missing sink.
    days_partial: int = 0
    bars_persisted: int = 0
    days: list[DayResult] = field(default_factory=list)

    @property
    def days_processed(self) -> int:
        return self.days_ok + self.days_filtered + self.days_missing + self.days_partial

    def to_summary(self) -> dict:
        return {
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "symbols_requested": self.symbols_requested,
            "days_listed": self.days_listed,
            "days_ok": self.days_ok,
            "days_missing": self.days_missing,
            "days_filtered": self.days_filtered,
            "days_skipped": self.days_skipped,
            "days_errored": self.days_errored,
            "days_partial": self.days_partial,
            "bars_persisted": self.bars_persisted,
        }


ProgressFn = Callable[[DayResult], None]


# ---------- service ----------


class FlatFilesBackfillService:
    """
    Stateless service that reads Polygon Flat Files and fans canonical
    day-frames out to one or more sinks.

    Instances are cheap to construct; the underlying
    ``PolygonFlatFilesClient`` is lazy-built on first use. Tests inject
    mocked clients and mocked sinks via the constructor.
    """

    DEFAULT_SOURCE_TAG = "polygon-flatfiles"
    DEFAULT_BATCH_SIZE = 1000

    def __init__(
        self,
        *,
        flat_files: Optional[PolygonFlatFilesClient] = None,
        sinks: Optional[Sequence[Sink]] = None,
        # Legacy back-compat: when ``sinks`` is None, build a default
        # ClickHouseSink from these args. Existing tests rely on this.
        insert_minute_fn: Optional[InsertFn] = None,
        insert_daily_fn: Optional[InsertFn] = None,
        source_tag: str = DEFAULT_SOURCE_TAG,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._flat_files = flat_files
        self._source_tag = (source_tag or self.DEFAULT_SOURCE_TAG).strip()
        if not self._source_tag:
            raise ValueError("FlatFilesBackfillService: source_tag cannot be empty")
        self._batch_size = max(1, int(batch_size))

        if sinks is not None:
            self._sinks: List[Sink] = list(sinks)
        else:
            # Legacy single-sink mode: only build a ClickHouseSink when
            # the caller provided insert functions. If neither is set,
            # leave the sink list empty (caller must configure later).
            if insert_minute_fn is not None or insert_daily_fn is not None:
                from app.db.queries import (
                    insert_bars_batch_async,
                    insert_daily_bars_batch_async,
                )
                self._sinks = [
                    ClickHouseSink(
                        insert_minute_fn=insert_minute_fn or insert_bars_batch_async,
                        insert_daily_fn=insert_daily_fn or insert_daily_bars_batch_async,
                        batch_size=self._batch_size,
                    )
                ]
            else:
                self._sinks = []

    # ---------- factory ----------

    @classmethod
    def from_settings(cls) -> "FlatFilesBackfillService":
        """
        Build the canonical instance from ``app.config.settings``.

        Sink wiring:
          - ClickHouseSink is always configured (the hot cache is the
            primary read path for the API and the alert engine)
          - LakeSink is appended when ``LAKE_ARCHIVE_ENABLED=true`` and
            ``STOCK_LAKE_BUCKET`` is non-empty. Missing creds fall
            through silently — operators see the warning in the log.

        Raises if the Polygon Flat Files credentials are missing — we'd
        rather fail fast than 403 the first download.
        """
        from app.config import settings

        client = PolygonFlatFilesClient.from_settings()
        sinks: list[Sink] = [
            ClickHouseSink.from_settings(batch_size=cls.DEFAULT_BATCH_SIZE),
        ]
        if settings.lake_archive_enabled:
            if settings.stock_lake_bucket:
                # Lazy import to avoid pulling boto3 into modules that
                # never run a backfill.
                from app.services.flatfiles_sinks import LakeSink
                try:
                    sinks.append(LakeSink.from_settings())
                except Exception as e:
                    # Misconfigured lake should never break a CH-only
                    # backfill — warn and continue with what we have.
                    logger.warning(
                        "FlatFilesBackfillService: LakeSink disabled "
                        "(failed to build from settings): %s", e,
                    )
            else:
                logger.warning(
                    "FlatFilesBackfillService: LAKE_ARCHIVE_ENABLED=true "
                    "but STOCK_LAKE_BUCKET is empty; LakeSink disabled."
                )
        return cls(flat_files=client, sinks=sinks)

    # ---------- properties ----------

    @property
    def source_tag(self) -> str:
        return self._source_tag

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def sinks(self) -> List[Sink]:
        return list(self._sinks)

    # ---------- internal ----------

    def _client(self) -> PolygonFlatFilesClient:
        if self._flat_files is None:
            self._flat_files = PolygonFlatFilesClient.from_settings()
        return self._flat_files

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
        """Strip / upper / dedupe while preserving order."""
        seen: set[str] = set()
        out: list[str] = []
        for s in symbols or []:
            if not s:
                continue
            t = s.strip().upper()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _canonicalize_frame(self, df: pd.DataFrame, *, kind: Kind) -> pd.DataFrame:
        """
        Convert a flat-file-shape DataFrame into the canonical shape
        every sink consumes.

        Flat-file columns (per ``PolygonFlatFilesClient``):
            ticker, volume, open, close, high, low, window_start,
            transactions (minute only), timestamp

        Canonical columns (kind="minute"):
            symbol, timestamp, open, high, low, close, volume, vwap,
            trade_count, source

        Canonical columns (kind="day"):
            symbol, timestamp, open, high, low, close, volume, source

        Rows missing any required OHLCV / timestamp value are dropped
        here so downstream sinks never have to re-filter.
        """
        if df is None or df.empty:
            return pd.DataFrame()

        cols = list(df.columns)
        required = {"ticker", "open", "high", "low", "close", "volume", "timestamp"}
        missing = required - set(cols)
        if missing:
            raise ValueError(
                f"flat-files frame missing required columns: {sorted(missing)}; "
                f"have={cols}"
            )

        # Drop bad rows up front. ``dropna`` on the OHLCV+timestamp
        # subset is fast and gives both sinks the same row count.
        clean = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        if clean.empty:
            return pd.DataFrame()

        if kind == "minute":
            tx = (
                clean["transactions"].fillna(0).astype("int64")
                if "transactions" in clean.columns
                else pd.Series([0] * len(clean), index=clean.index, dtype="int64")
            )
            out = pd.DataFrame({
                "symbol": clean["ticker"].astype(str).str.upper(),
                "timestamp": _ensure_utc_series(clean["timestamp"]),
                "open": clean["open"].astype("float64"),
                "high": clean["high"].astype("float64"),
                "low": clean["low"].astype("float64"),
                "close": clean["close"].astype("float64"),
                "volume": clean["volume"].astype("float64"),
                # Flat files do not carry VWAP; default to 0.0 so the
                # canonical schema stays consistent across providers.
                "vwap": 0.0,
                "trade_count": tx,
                "source": self._source_tag,
            })
        else:
            out = pd.DataFrame({
                "symbol": clean["ticker"].astype(str).str.upper(),
                "timestamp": _ensure_utc_series(clean["timestamp"]),
                "open": clean["open"].astype("float64"),
                "high": clean["high"].astype("float64"),
                "low": clean["low"].astype("float64"),
                "close": clean["close"].astype("float64"),
                "volume": clean["volume"].astype("float64"),
                "source": self._source_tag,
            })
        out.reset_index(drop=True, inplace=True)
        return out

    # ---- backward-compat shims (existing tests pin behaviour) ----

    def _df_to_minute_records(self, df: pd.DataFrame) -> list[dict]:
        """Compatibility shim: flat-file frame → canonical → records.
        Kept so the pre-C2 transform tests still pass without rewriting."""
        canonical = self._canonicalize_frame(df, kind="minute")
        return _frame_to_records(canonical, kind="minute")

    def _df_to_daily_records(self, df: pd.DataFrame) -> list[dict]:
        canonical = self._canonicalize_frame(df, kind="day")
        return _frame_to_records(canonical, kind="day")

    async def _insert_batched(self, records: list[dict], *, kind: Kind) -> int:
        """Compatibility shim for callers that still drive inserts
        directly. Routes through the first ClickHouseSink in the list;
        falls back to a no-op if none is configured."""
        if not records:
            return 0
        for s in self._sinks:
            if isinstance(s, ClickHouseSink):
                # ClickHouseSink._insert_batched returns batch count;
                # the legacy contract returns record count. Mimic that.
                await s._insert_batched(records, kind=kind)
                return len(records)
        # No ClickHouseSink configured — silently no-op so older callers
        # don't crash, but log so it's visible.
        logger.warning(
            "FlatFilesBackfillService._insert_batched called but no "
            "ClickHouseSink is configured; skipping.",
        )
        return 0

    # ---------- per-day processing ----------

    async def _process_day(
        self,
        d: date,
        *,
        symbols: list[str],
        kind: Kind,
        dry_run: bool,
    ) -> DayResult:
        """Download → canonicalise → fan out to sinks for one date. All
        exceptions are captured into the returned ``DayResult`` so range
        processing can continue past per-day failures."""
        start_ts = datetime.now(timezone.utc)
        result = DayResult(file_date=d)
        try:
            client = self._client()
            sym_filter = symbols or None  # None == no filter in client
            if kind == "minute":
                df = await asyncio.to_thread(
                    client.download_minute_aggs, d, symbols=sym_filter,
                )
            else:
                df = await asyncio.to_thread(
                    client.download_day_aggs, d, symbols=sym_filter,
                )

            if df is None or df.empty:
                # Two reasons a frame can be empty:
                #   1) No file exists for ``d`` (404 silently absorbed by
                #      the client). That's a weekend / holiday / pre-IPO.
                #   2) File existed but the symbol filter eliminated every
                #      row. That's "filtered".
                # Heuristic: no symbol filter ⇒ "missing".
                if not symbols:
                    result.status = "missing"
                else:
                    result.status = "filtered"
                return result

            result.symbols_seen = int(df["ticker"].nunique())
            if dry_run:
                result.status = "skipped"
                result.bars_persisted = 0
                return result

            canonical = self._canonicalize_frame(df, kind=kind)
            if canonical.empty:
                # Every row dropped during NaN cleanup. Treat as filtered
                # — the file was downloaded successfully but had no
                # usable data after sanitisation.
                result.status = "filtered"
                return result

            if not self._sinks:
                # No-sink mode is legitimate during dry validation runs;
                # we treat it as "skipped" so accounting stays clean.
                result.status = "skipped"
                return result

            sink_results = await self._fan_out(canonical, d, kind)
            result.sink_results = sink_results
            result.status, result.error, result.bars_persisted = (
                _aggregate_sink_status(sink_results)
            )
            return result
        except PolygonFlatFilesError as e:
            # S3-side failure (auth, network). Per-day error so the
            # surrounding range can continue. Caller decides whether to
            # retry the day later.
            result.status = "error"
            result.error = str(e)
            logger.exception("flat-files backfill %s %s: %s", kind, d, e)
            return result
        except Exception as e:
            # Transform / unexpected failure. Same isolation policy.
            result.status = "error"
            result.error = str(e)
            logger.exception("flat-files backfill %s %s: %s", kind, d, e)
            return result
        finally:
            result.elapsed_s = (
                datetime.now(timezone.utc) - start_ts
            ).total_seconds()

    async def _fan_out(
        self,
        canonical: pd.DataFrame,
        d: date,
        kind: Kind,
    ) -> dict[str, SinkResult]:
        """Drive every configured sink with the canonical frame.
        Sinks are independent — one's failure does not stop the next."""
        out: dict[str, SinkResult] = {}
        for sink in self._sinks:
            try:
                sr = await sink.write(
                    canonical, file_date=d, kind=kind,
                    provider=self._source_tag,
                )
            except Exception as e:
                # Defence-in-depth: sinks should classify their own
                # errors, but a misbehaving custom sink that raises must
                # NOT take down the whole run.
                logger.exception(
                    "flat-files: sink %r raised on %s %s: %s",
                    sink.name, kind, d, e,
                )
                sr = SinkResult(
                    sink=sink.name, status="error", bars_written=0,
                    error=str(e),
                )
            out[sink.name] = sr
        return out

    # ---------- public API ----------

    async def backfill_range(
        self,
        symbols: Iterable[str],
        start: date,
        end: date,
        *,
        kind: Kind = "minute",
        dry_run: bool = False,
        on_progress: Optional[ProgressFn] = None,
        concurrency: int = 1,
        skip_dates: Optional[Iterable[date]] = None,
    ) -> BackfillRangeResult:
        """
        Backfill ``symbols`` from Polygon Flat Files for every available
        trading day in ``[start, end]``. Set ``symbols=[]`` to ingest the
        full US-equities universe present in each file.

        ``on_progress`` is invoked exactly once per processed day with
        the ``DayResult`` so a CLI can stream a live indicator. Under
        ``concurrency > 1`` callbacks fire in *completion* order, not
        date order.

        ``concurrency`` bounds how many days are in flight at once. 1
        (default) preserves the legacy serial behaviour; higher values
        let the seed exploit the natural I/O parallelism between
        Polygon S3 downloads and ClickHouse / lake writes. Memory peak
        is roughly ``concurrency × peak-day-frame`` — 4 workers on the
        full US tape uses ~600 MB.

        ``skip_dates`` is a set of pre-computed dates the caller knows
        are already done (e.g. from a watermark scan). Skipped dates do
        not count toward any bucket; ``result.days_listed`` reflects
        the post-filter day count.
        """
        if kind not in ("minute", "day"):
            raise ValueError(f"unsupported kind: {kind!r}")
        if end < start:
            raise ValueError(f"end ({end}) is before start ({start})")
        if concurrency < 1:
            raise ValueError(f"concurrency must be >= 1, got {concurrency}")

        sym_list = self._normalize_symbols(symbols)
        result = BackfillRangeResult(
            kind=kind, start=start, end=end,
            symbols_requested=len(sym_list),
        )

        # ``available_dates`` is the canonical list — it skips holidays
        # without needing a calendar and surfaces any S3-layer issue
        # (auth / endpoint) before we start downloading.
        try:
            files = await asyncio.to_thread(
                self._client().available_dates, start, end, kind=kind,
            )
        except PolygonFlatFilesError as e:
            logger.error("flat-files backfill: listing failed for %s..%s: %s",
                         start, end, e)
            raise

        # Apply caller's skip set BEFORE we count days_listed so the
        # summary reflects what the run will actually process.
        if skip_dates:
            skip_set = {d for d in skip_dates}
            filtered_files = [f for f in files if f.file_date not in skip_set]
            skipped_count = len(files) - len(filtered_files)
            if skipped_count:
                logger.info(
                    "flat-files backfill: skipping %d already-archived day(s) "
                    "(resumed via watermark pre-scan)", skipped_count,
                )
            files = filtered_files

        result.days_listed = len(files)
        if not files:
            logger.info(
                "flat-files backfill: no %s files between %s and %s "
                "(weekend / holiday / out-of-range / all-already-archived)",
                kind, start, end,
            )
            return result

        if concurrency == 1:
            await self._run_serial(
                files, sym_list, kind, dry_run, result, on_progress,
            )
        else:
            await self._run_concurrent(
                files, sym_list, kind, dry_run, result, on_progress,
                concurrency=concurrency,
            )

        # Deterministic ordering for downstream consumers regardless of
        # the execution mode. The bookkeeping counters above are order-
        # independent so this sort is a pure presentation tidy-up.
        result.days.sort(key=lambda d: d.file_date)

        logger.info(
            "flat-files backfill complete: kind=%s window=%s..%s "
            "ok=%d partial=%d filtered=%d missing=%d errored=%d "
            "skipped=%d bars=%d concurrency=%d",
            kind, start, end, result.days_ok, result.days_partial,
            result.days_filtered, result.days_missing, result.days_errored,
            result.days_skipped, result.bars_persisted, concurrency,
        )
        return result

    # ---------- execution strategies ----------

    async def _run_serial(
        self,
        files: list,
        sym_list: list[str],
        kind: Kind,
        dry_run: bool,
        result: "BackfillRangeResult",
        on_progress: Optional[ProgressFn],
    ) -> None:
        for info in files:
            day = await self._process_day(
                info.file_date, symbols=sym_list,
                kind=kind, dry_run=dry_run,
            )
            self._accumulate_day(result, day, on_progress)

    async def _run_concurrent(
        self,
        files: list,
        sym_list: list[str],
        kind: Kind,
        dry_run: bool,
        result: "BackfillRangeResult",
        on_progress: Optional[ProgressFn],
        *,
        concurrency: int,
    ) -> None:
        """Bounded-parallel execution. Uses a semaphore so peak memory
        stays at ``concurrency × peak-day-frame`` instead of unbounded.
        Results stream back in completion order so progress callbacks
        update in real time."""
        sem = asyncio.Semaphore(concurrency)

        async def _bounded(info) -> DayResult:
            async with sem:
                return await self._process_day(
                    info.file_date, symbols=sym_list,
                    kind=kind, dry_run=dry_run,
                )

        tasks = [asyncio.create_task(_bounded(info)) for info in files]
        try:
            for coro in asyncio.as_completed(tasks):
                day = await coro
                self._accumulate_day(result, day, on_progress)
        except BaseException:
            # Cancel any in-flight tasks on abort (KeyboardInterrupt,
            # parent cancellation) so we don't leak running coroutines.
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise

    @staticmethod
    def _accumulate_day(
        result: "BackfillRangeResult",
        day: DayResult,
        on_progress: Optional[ProgressFn],
    ) -> None:
        """Update bucket counters + fire progress callback. Pulled out
        of the loops so serial and parallel paths share one accounting
        contract."""
        result.days.append(day)
        result.bars_persisted += day.bars_persisted

        if day.status == "ok":
            result.days_ok += 1
        elif day.status == "partial":
            result.days_partial += 1
        elif day.status == "filtered":
            result.days_filtered += 1
        elif day.status == "missing":
            result.days_missing += 1
        elif day.status == "skipped":
            result.days_skipped += 1
        else:
            result.days_errored += 1

        if on_progress is not None:
            try:
                on_progress(day)
            except Exception:
                # A buggy progress callback must NEVER abort the
                # backfill — log and continue.
                logger.exception(
                    "flat-files backfill: progress callback raised",
                )


# ---------- helpers ----------


def _ensure_utc_series(series: pd.Series) -> pd.Series:
    """Coerce a timestamp series to tz-aware UTC. Handles all three
    common shapes we see from the parquet/CSV path: naive,
    already-UTC, and other-tz."""
    s = pd.to_datetime(series, utc=False, errors="coerce")
    if hasattr(s, "dt") and s.dt.tz is None:
        s = s.dt.tz_localize("UTC")
    else:
        s = s.dt.tz_convert("UTC")
    return s


def _aggregate_sink_status(
    sink_results: dict[str, SinkResult],
) -> tuple[str, Optional[str], int]:
    """
    Reduce per-sink statuses into ``(day_status, day_error, bars)``.

    Rules:
      - all sinks ok        → ("ok", None, max(bars across sinks))
      - some ok, some err   → ("partial", "sink_a: ...; sink_b: ...", max(bars across ok sinks))
      - all sinks err       → ("error",   "sink_a: ...; sink_b: ...", 0)
      - mix ok + skipped    → ("ok", None, ...) (skipped is not a failure)
      - all sinks skipped   → ("skipped", None, 0)
    """
    if not sink_results:
        return ("skipped", None, 0)

    statuses = [r.status for r in sink_results.values()]
    has_ok = any(s == "ok" for s in statuses)
    has_err = any(s == "error" for s in statuses)
    has_only_skipped = all(s == "skipped" for s in statuses)

    if has_only_skipped:
        return ("skipped", None, 0)

    if has_ok and not has_err:
        bars = max((r.bars_written for r in sink_results.values() if r.status == "ok"), default=0)
        return ("ok", None, bars)

    if has_err and not has_ok:
        msg = "; ".join(
            f"{r.sink}: {r.error or 'error'}"
            for r in sink_results.values() if r.status == "error"
        )
        return ("error", msg, 0)

    # Mix of ok + error: partial. bars = max across successful sinks.
    bars = max((r.bars_written for r in sink_results.values() if r.status == "ok"), default=0)
    msg = "; ".join(
        f"{r.sink}: {r.error or 'error'}"
        for r in sink_results.values() if r.status == "error"
    )
    return ("partial", msg, bars)


__all__ = [
    "BackfillRangeResult",
    "DayResult",
    "FlatFilesBackfillService",
    "Kind",
]
