"""
Bulk historical backfill from Polygon Flat Files into ClickHouse.

The contract is intentionally narrow:

    "Given a symbol universe and a date range, load Polygon's flat-file
    aggregates for those days into ``ohlcv_1m`` / ``ohlcv_daily`` and
    return per-date stats."

That's it. This service does NOT:

  - Touch the ``S3LakeClient`` (that's Phase C — archive worker).
  - Couple to the existing ``BackfillService`` (which is REST-aware and
    coverage-driven). A future commit may chain them via
    ``BackfillService.enqueue_deep`` for older windows when the configured
    history provider is ``polygon``, but the two services stay separate
    so this one is testable and runnable as a standalone CLI.
  - Detect gaps. Flat files are the gold-standard *bulk* source; surgical
    intraday gap fills stay on the REST path.

Design notes
------------
* **Per-day processing** keeps memory bounded. The full US-equities tape
  is ~1.9M rows / ~28MB compressed per day; with symbol pre-filtering
  inside ``PolygonFlatFilesClient`` the in-process frame for the seed-100
  drops to ~50–100k rows per day.
* **Provenance** lives in the ``source`` column. Defaults to
  ``polygon-flatfiles`` so the lake archive can route deltas to the
  ``raw/provider=polygon-flatfiles/`` partition independently from
  REST-fetched ``polygon`` bars.
* **Idempotency** is provided by ClickHouse: ``ReplacingMergeTree(version)``
  on ``(symbol, timestamp)`` dedupes re-runs of the same day. Re-running
  is therefore a safe no-op when nothing has changed.
* **Async front, sync inserts** matches the rest of the codebase: the
  service exposes ``backfill_range()`` as ``async`` so it can be driven
  from FastAPI background tasks, but the actual ClickHouse insert calls
  are sync functions (the same ones the existing BackfillService uses).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Iterable, Literal, Optional

import pandas as pd

from app.db import queries
from app.providers.polygon_flatfiles import (
    PolygonFlatFilesClient,
    PolygonFlatFilesError,
)

logger = logging.getLogger(__name__)


Kind = Literal["minute", "day"]


# Async insert callable signature used by both 1m and daily paths.
InsertFn = Callable[[list[dict]], Awaitable[None]]


@dataclass(slots=True)
class DayResult:
    """Outcome of one date's flat-file backfill. Aggregated into
    ``BackfillRangeResult`` by ``backfill_range``."""
    file_date: date
    bars_persisted: int = 0
    symbols_seen: int = 0
    # Status is one of:
    #   "ok"       — file found, parsed, persisted
    #   "missing"  — no file for this date (weekend / holiday / pre-IPO)
    #   "filtered" — file present but the symbol filter matched 0 rows
    #   "error"    — download or insert failure (see ``error`` for cause)
    #   "skipped"  — caller asked us to skip (e.g. dry-run)
    status: str = "ok"
    error: Optional[str] = None
    elapsed_s: float = 0.0


@dataclass(slots=True)
class BackfillRangeResult:
    """Aggregated outcome of ``backfill_range``. Suitable for
    serialization to JSON for the CLI summary."""
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
    bars_persisted: int = 0
    days: list[DayResult] = field(default_factory=list)

    @property
    def days_processed(self) -> int:
        return self.days_ok + self.days_filtered + self.days_missing

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
            "bars_persisted": self.bars_persisted,
        }


# Callback type for per-day progress reporting (CLI hook).
ProgressFn = Callable[[DayResult], None]


class FlatFilesBackfillService:
    """
    Stateless service that reads Polygon Flat Files and writes ClickHouse.

    Instances are cheap to construct; the underlying ``PolygonFlatFilesClient``
    is lazy-built on first use. Tests inject mocks via the constructor and
    never touch real S3 or ClickHouse.
    """

    DEFAULT_SOURCE_TAG = "polygon-flatfiles"
    DEFAULT_BATCH_SIZE = 1000

    def __init__(
        self,
        *,
        flat_files: Optional[PolygonFlatFilesClient] = None,
        insert_minute_fn: Optional[InsertFn] = None,
        insert_daily_fn: Optional[InsertFn] = None,
        source_tag: str = DEFAULT_SOURCE_TAG,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._flat_files = flat_files
        # Defaults wire to the same insert path the BackfillService uses, so
        # rows land in the existing partitions with the same dedup contract.
        self._insert_minute_fn: InsertFn = (
            insert_minute_fn or queries.insert_bars_batch_async
        )
        self._insert_daily_fn: InsertFn = (
            insert_daily_fn or queries.insert_daily_bars_batch_async
        )
        self._source_tag = (source_tag or self.DEFAULT_SOURCE_TAG).strip()
        if not self._source_tag:
            raise ValueError("FlatFilesBackfillService: source_tag cannot be empty")
        self._batch_size = max(1, int(batch_size))

    @classmethod
    def from_settings(cls) -> "FlatFilesBackfillService":
        """
        Build the canonical instance using ``app.config.settings``. Lets
        callers (CLI, future enqueue path) construct without thinking about
        wiring. Raises if the Polygon Flat Files credentials are missing —
        we'd rather fail fast than 403 the first download.
        """
        return cls(flat_files=PolygonFlatFilesClient.from_settings())

    # ---------- properties (helpful for tests / CLI) ----------

    @property
    def source_tag(self) -> str:
        return self._source_tag

    @property
    def batch_size(self) -> int:
        return self._batch_size

    # ---------- internal ----------

    def _client(self) -> PolygonFlatFilesClient:
        if self._flat_files is None:
            self._flat_files = PolygonFlatFilesClient.from_settings()
        return self._flat_files

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
        """Strip / upper / dedupe while preserving order. Empty iterable
        returns ``[]`` which downstream treats as 'all symbols in file'."""
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

    def _df_to_minute_records(self, df: pd.DataFrame) -> list[dict]:
        """
        Convert a flat-file 1-min DataFrame into the row shape expected by
        ``queries.insert_bars_batch_async``. ``vwap`` defaults to ``0``
        because Polygon's minute flat files do not ship VWAP; ``trade_count``
        comes from ``transactions`` (NaN → 0).
        """
        if df.empty:
            return []
        # Coerce nullable Int64 transactions to plain int with NaN handling.
        # ``fillna(0).astype(int)`` is the most defensive form across pandas
        # versions (some return Int64, some int64, depending on input dtype).
        tx = (
            df["transactions"].fillna(0).astype("int64")
            if "transactions" in df.columns
            else pd.Series([0] * len(df), index=df.index, dtype="int64")
        )
        records: list[dict] = []
        ts_series = df["timestamp"]
        ticker = df["ticker"]
        o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; v = df["volume"]
        src = self._source_tag
        # Use itertuples-style positional access for speed (~3x faster than
        # df.iterrows on a 1M-row frame). We zip column Series directly so
        # we don't materialise an intermediate frame view per row.
        for sym, ts, op, hi, lo, cl, vol, t_count in zip(
            ticker, ts_series, o, h, l, c, v, tx,
        ):
            # ``ts`` is a pandas Timestamp (UTC); ClickHouse driver accepts
            # this directly. NaN/None guards keep a single dirty row from
            # poisoning the whole batch.
            if pd.isna(ts):
                continue
            if any(pd.isna(x) for x in (op, hi, lo, cl, vol)):
                continue
            py_ts: datetime = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if py_ts.tzinfo is None:
                py_ts = py_ts.replace(tzinfo=timezone.utc)
            records.append({
                "symbol": str(sym),
                "timestamp": py_ts,
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),
                "volume": float(vol),
                "vwap": 0.0,
                "trade_count": int(t_count) if not pd.isna(t_count) else 0,
                "source": src,
            })
        return records

    def _df_to_daily_records(self, df: pd.DataFrame) -> list[dict]:
        """Daily variant. The daily schema has no ``vwap`` / ``trade_count``
        columns, so we omit them. ``insert_daily_bars_batch_async`` ignores
        any extras anyway, but the simpler payload is easier to debug."""
        if df.empty:
            return []
        records: list[dict] = []
        ts_series = df["timestamp"]
        ticker = df["ticker"]
        o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; v = df["volume"]
        src = self._source_tag
        for sym, ts, op, hi, lo, cl, vol in zip(
            ticker, ts_series, o, h, l, c, v,
        ):
            if pd.isna(ts):
                continue
            if any(pd.isna(x) for x in (op, hi, lo, cl, vol)):
                continue
            py_ts: datetime = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if py_ts.tzinfo is None:
                py_ts = py_ts.replace(tzinfo=timezone.utc)
            records.append({
                "symbol": str(sym),
                "timestamp": py_ts,
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),
                "volume": float(vol),
                "source": src,
            })
        return records

    async def _insert_batched(self, records: list[dict], *, kind: Kind) -> int:
        """Insert ``records`` in fixed-size batches. Returns the number
        actually persisted (== len(records); we don't dedupe here, ClickHouse
        does)."""
        if not records:
            return 0
        insert_fn = (
            self._insert_minute_fn if kind == "minute" else self._insert_daily_fn
        )
        n = len(records)
        for i in range(0, n, self._batch_size):
            await insert_fn(records[i : i + self._batch_size])
        return n

    async def _process_day(
        self,
        d: date,
        *,
        symbols: list[str],
        kind: Kind,
        dry_run: bool,
    ) -> DayResult:
        """Download → parse → insert a single date. All exceptions are
        captured into the returned ``DayResult`` so range processing can
        continue past per-day failures."""
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
                #      row. That's "filtered" — different operationally.
                # We distinguish by re-checking against the listing, but a
                # cheap heuristic (no symbol filter ⇒ "missing") covers
                # the common case without a second S3 call.
                if not symbols:
                    result.status = "missing"
                else:
                    # When filtering, an empty frame is ambiguous; treat as
                    # "filtered" so callers can see the file did exist if
                    # they read the warning log. The downstream summary
                    # counts these separately from genuine missing days.
                    result.status = "filtered"
                return result

            result.symbols_seen = int(df["ticker"].nunique())
            if dry_run:
                result.status = "skipped"
                result.bars_persisted = 0
                return result

            if kind == "minute":
                records = self._df_to_minute_records(df)
            else:
                records = self._df_to_daily_records(df)
            persisted = await self._insert_batched(records, kind=kind)
            result.bars_persisted = persisted
            result.status = "ok"
            return result
        except PolygonFlatFilesError as e:
            # Treat S3-side failures (auth, network) as a per-day error so
            # the surrounding range can continue. The CLI / caller decides
            # whether to retry the day later.
            result.status = "error"
            result.error = str(e)
            logger.exception("flat-files backfill %s %s: %s", kind, d, e)
            return result
        except Exception as e:
            # ClickHouse / transform-side failure. Same isolation policy as
            # the S3-side branch.
            result.status = "error"
            result.error = str(e)
            logger.exception("flat-files backfill %s %s: %s", kind, d, e)
            return result
        finally:
            result.elapsed_s = (
                datetime.now(timezone.utc) - start_ts
            ).total_seconds()

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
    ) -> BackfillRangeResult:
        """
        Backfill ``symbols`` from Polygon Flat Files for every available
        trading day in ``[start, end]``. Set ``symbols=[]`` to ingest the
        full US-equities universe present in each file.

        Returns a ``BackfillRangeResult`` capturing per-date outcomes; the
        caller's CLI / API surface is free to summarise that however it
        likes. ``on_progress`` is invoked exactly once per day with the
        ``DayResult`` so a CLI can stream a live "12/250 done" indicator.
        """
        if kind not in ("minute", "day"):
            raise ValueError(f"unsupported kind: {kind!r}")
        if end < start:
            raise ValueError(f"end ({end}) is before start ({start})")

        sym_list = self._normalize_symbols(symbols)
        result = BackfillRangeResult(
            kind=kind, start=start, end=end,
            symbols_requested=len(sym_list),
        )

        # We rely on ``available_dates`` for the canonical list of days
        # rather than iterating weekdays ourselves — it correctly skips
        # holidays without a holiday calendar and surfaces any S3-layer
        # issue (auth / endpoint) before we start downloading.
        try:
            files = await asyncio.to_thread(
                self._client().available_dates, start, end, kind=kind,
            )
        except PolygonFlatFilesError as e:
            logger.error("flat-files backfill: listing failed for %s..%s: %s",
                         start, end, e)
            raise

        result.days_listed = len(files)
        if not files:
            logger.info(
                "flat-files backfill: no %s files between %s and %s "
                "(weekend / holiday / out-of-range)", kind, start, end,
            )
            return result

        for info in files:
            day = await self._process_day(
                info.file_date,
                symbols=sym_list,
                kind=kind,
                dry_run=dry_run,
            )
            result.days.append(day)
            result.bars_persisted += day.bars_persisted

            if day.status == "ok":
                result.days_ok += 1
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

        logger.info(
            "flat-files backfill complete: kind=%s window=%s..%s "
            "days_ok=%d filtered=%d missing=%d errored=%d skipped=%d bars=%d",
            kind, start, end, result.days_ok, result.days_filtered,
            result.days_missing, result.days_errored, result.days_skipped,
            result.bars_persisted,
        )
        return result


# Re-export a memoised instance for convenience. NOT auto-initialised: the
# constructor builds the S3 client lazily, but ``from_settings`` raises if
# credentials are missing and we don't want module-import to crash a
# misconfigured deploy. Callers should use ``FlatFilesBackfillService.from_settings()``
# explicitly when they need it.
__all__ = [
    "BackfillRangeResult",
    "DayResult",
    "FlatFilesBackfillService",
    "Kind",
]
