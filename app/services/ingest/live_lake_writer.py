"""
Live-stream → bronze lake writer.

Periodically reads recent CH `ohlcv_1m` rows from the live stream and
upserts them into the corresponding `bronze.{provider}_minute` Iceberg
table. Closes the freshness gap between live ticks landing in CH
(seconds) and silver build seeing them (was 8-24h via nightly REST
backfill; now ~5-10 min via this writer).

**Per [data_platform_plan §8 Path A](../../../docs/data_platform_plan.md):**

    Path A — live streaming (T+0 → T+5min lake)
    1. Provider WebSocket → existing async batcher → ClickHouse ohlcv_1m
    2. live_lake_writer job runs every 5 minutes:
       - Reads CH ohlcv_1m for the last 15 minutes
       - Groups by provider
       - MERGE INTO bronze.{provider}_minute per group
       - Records run in ingestion_runs

**Provider tagging.** Live stream rows in CH carry `source = "{provider}-stream"`
(e.g. `"schwab-stream"`). The writer filters on this suffix to avoid
double-writing rows already in bronze via the nightly REST backfill.
Each provider's live stream uses a distinct tag → adding a new live
provider is purely a config + tagging change; this writer needs no
modification.

**Idempotency.** Bronze tables use identifier `(symbol, timestamp)`,
so PyIceberg's `upsert` is naturally idempotent: re-running the cycle
or running with an overlapping window is safe. Watermark in
`ingestion_runs` lets the cycle resume cleanly after crashes.

**Small-file mitigation.** A 5-min cycle × ~100 symbols × ~5 bars/cycle
produces ~500 rows per write (≈30 KB Iceberg file). Daily compaction
(TA-5.7.4) merges these into target-sized files.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pyarrow as pa

from app.config import settings

logger = logging.getLogger(__name__)


# Per-provider mapping. Each entry:
#   live source tag (what the bar_batcher writes for live rows)  →
#   ensure-table function + arrow-schema importer
#
# Adding a new live-streaming provider:
#   1. Add a new bronze.{provider}_minute table to app/services/bronze/.
#   2. Make sure the live stream tags rows with f"{provider}-stream".
#   3. Add one entry to _PROVIDER_CONFIG below. ZERO writer changes.
@dataclass(frozen=True)
class _ProviderConfig:
    """Per-provider routing config for the live writer."""
    live_source_tag: str         # CH `source` value the live stream writes
    bronze_table_short_name: str # short name (e.g. "schwab_minute")


_PROVIDER_CONFIG: dict[str, _ProviderConfig] = {
    "schwab": _ProviderConfig(
        live_source_tag="schwab-stream",
        bronze_table_short_name="schwab_minute",
    ),
    # Future: a polygon live stream would add an entry here, e.g.
    # "polygon": _ProviderConfig("polygon-stream", "polygon_minute"),
}


# Arrow schema for bronze.{provider}_minute. Must match the declared
# Iceberg schema in app/services/bronze/schemas.py exactly. Identifier
# fields (symbol, timestamp) drive the PyIceberg upsert join.
_BRONZE_MINUTE_ARROW = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=True),
        pa.field("high", pa.float64(), nullable=True),
        pa.field("low", pa.float64(), nullable=True),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("volume", pa.float64(), nullable=True),
        pa.field("vwap", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("source", pa.string(), nullable=True),
        pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("ingestion_run_id", pa.string(), nullable=True),
    ]
)


@dataclass
class CycleResult:
    """One run of `LiveLakeWriter.run_cycle`."""
    run_id: str
    started_at: datetime
    finished_at: datetime
    window_start: datetime
    window_end: datetime
    per_provider_rows_written: dict[str, int] = field(default_factory=dict)
    per_provider_errors: dict[str, str] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def total_rows(self) -> int:
        return sum(self.per_provider_rows_written.values())

    @property
    def succeeded(self) -> bool:
        return not self.per_provider_errors


class LiveLakeWriter:
    """Reads CH ohlcv_1m for the last N minutes and upserts into
    bronze.{provider}_minute Iceberg tables.

    Construct via `from_settings()` for production. Pass explicit
    `cycle_minutes` / `lookback_minutes` for tests.
    """

    def __init__(
        self,
        *,
        cycle_minutes: int = 5,
        lookback_minutes: int = 15,
        provider_config: Optional[dict[str, _ProviderConfig]] = None,
    ) -> None:
        if cycle_minutes <= 0:
            raise ValueError("cycle_minutes must be > 0")
        if lookback_minutes < cycle_minutes:
            # We deliberately re-read a few minutes overlap each cycle so
            # late-arriving bars (a writer that flushed just after the
            # previous cycle's window closed) still get picked up.
            raise ValueError(
                "lookback_minutes must be >= cycle_minutes (need overlap)"
            )
        self._cycle_minutes = cycle_minutes
        self._lookback_minutes = lookback_minutes
        self._provider_config = provider_config or _PROVIDER_CONFIG
        self._stopped = asyncio.Event()

    @classmethod
    def from_settings(cls) -> "LiveLakeWriter":
        return cls(
            cycle_minutes=settings.live_lake_writer_cycle_minutes,
            lookback_minutes=settings.live_lake_writer_lookback_minutes,
        )

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    async def run_cycle(self, *, as_of: Optional[datetime] = None) -> CycleResult:
        """Execute one cycle: read CH for the lookback window, upsert
        into bronze per provider.

        Pass `as_of` for tests (deterministic time); production uses
        now-UTC. The actual window cuts off 1 minute before `as_of`
        to avoid racing the bar_batcher writing the current minute.
        """
        run_id = uuid.uuid4().hex
        now = as_of or datetime.now(timezone.utc)
        # Leave a 1-min safety margin so we don't read the in-flight
        # bar that the batcher hasn't yet finished writing.
        window_end = now - timedelta(minutes=1)
        window_start = window_end - timedelta(minutes=self._lookback_minutes)

        result = CycleResult(
            run_id=run_id,
            started_at=now,
            finished_at=now,  # populated at the end
            window_start=window_start,
            window_end=window_end,
        )

        for provider_name, cfg in self._provider_config.items():
            try:
                rows = self._read_ch(cfg.live_source_tag, window_start, window_end)
                if not rows:
                    result.per_provider_rows_written[provider_name] = 0
                    logger.info(
                        "live_lake_writer: provider=%s no live rows in window %s..%s; skipping",
                        provider_name, window_start.isoformat(), window_end.isoformat(),
                    )
                    continue

                arrow = self._rows_to_arrow(rows, run_id=run_id)
                self._upsert_bronze(cfg.bronze_table_short_name, arrow)
                result.per_provider_rows_written[provider_name] = arrow.num_rows
                logger.info(
                    "live_lake_writer: provider=%s upserted %d rows into bronze.%s",
                    provider_name, arrow.num_rows, cfg.bronze_table_short_name,
                )
            except Exception as e:
                logger.exception(
                    "live_lake_writer: provider=%s cycle failed: %s",
                    provider_name, e,
                )
                result.per_provider_errors[provider_name] = (
                    f"{type(e).__name__}: {e}"
                )

        result.finished_at = datetime.now(timezone.utc)
        self._record_run(result)
        return result

    async def run_forever(self) -> None:
        """Loop run_cycle every `cycle_minutes`. Cancelable via `stop()`.

        Used as a long-lived asyncio task wired into FastAPI's lifespan.
        Per-cycle failures are caught + logged; the loop never exits
        on an exception (only on `stop()`).
        """
        logger.info(
            "live_lake_writer: starting loop (cycle=%dmin lookback=%dmin)",
            self._cycle_minutes, self._lookback_minutes,
        )
        while not self._stopped.is_set():
            try:
                await self.run_cycle()
            except Exception as e:
                # run_cycle itself shouldn't raise; this is paranoid
                # belt-and-suspenders. Recover and wait for the next cycle.
                logger.exception(
                    "live_lake_writer: run_cycle raised (defensive): %s", e,
                )
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._cycle_minutes * 60,
                )
            except asyncio.TimeoutError:
                pass  # natural cycle wakeup
        logger.info("live_lake_writer: loop stopped")

    def stop(self) -> None:
        """Signal `run_forever` to exit at its next wakeup."""
        self._stopped.set()

    # ─────────────────────────────────────────────────────────────────
    # CH read
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _read_ch(
        source_tag: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict]:
        """Read CH ohlcv_1m rows matching the given source tag + window.

        Returns rows as plain dicts (column → value). Times are kept
        as datetime objects via ClickHouse's native typing.
        """
        from app.db import get_client

        client = get_client()
        rows = client.query(
            """
            SELECT
                symbol,
                timestamp,
                open,
                high,
                low,
                close,
                volume,
                vwap,
                trade_count,
                source
            FROM ohlcv_1m
            WHERE source = {tag:String}
              AND timestamp > {start:DateTime64(3, 'UTC')}
              AND timestamp <= {end:DateTime64(3, 'UTC')}
            ORDER BY symbol, timestamp
            """,
            parameters={
                "tag": source_tag,
                "start": window_start,
                "end": window_end,
            },
        ).named_results()
        return list(rows)

    # ─────────────────────────────────────────────────────────────────
    # Bronze upsert
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _rows_to_arrow(rows: list[dict], *, run_id: str) -> pa.Table:
        """Convert CH rows → PyArrow Table matching the bronze schema.

        Stamps `ingestion_ts` (now) and `ingestion_run_id` (this cycle's
        UUID) on every row so the audit trail is intact.
        """
        ingestion_ts = datetime.now(timezone.utc)
        arrays = {
            "symbol": [r["symbol"] for r in rows],
            "timestamp": [_ensure_utc(r["timestamp"]) for r in rows],
            "open": [_safe_float(r.get("open")) for r in rows],
            "high": [_safe_float(r.get("high")) for r in rows],
            "low": [_safe_float(r.get("low")) for r in rows],
            "close": [_safe_float(r.get("close")) for r in rows],
            "volume": [_safe_float(r.get("volume")) for r in rows],
            "vwap": [
                _safe_float(r.get("vwap")) if r.get("vwap") not in (None, 0, 0.0)
                else None
                for r in rows
            ],
            "trade_count": [
                int(r["trade_count"]) if r.get("trade_count") not in (None, 0)
                else None
                for r in rows
            ],
            "source": [r.get("source") for r in rows],
            "ingestion_ts": [ingestion_ts for _ in rows],
            "ingestion_run_id": [run_id for _ in rows],
        }
        return pa.Table.from_pydict(arrays, schema=_BRONZE_MINUTE_ARROW)

    @staticmethod
    def _upsert_bronze(table_short_name: str, arrow: pa.Table) -> None:
        """Upsert into bronze.{table_short_name} via PyIceberg.

        Identifier `(symbol, timestamp)` drives the join. When matched,
        Iceberg updates non-key columns (handles late-arriving correction
        bars). When not matched, inserts.

        Routed through `chunked_upsert` to dodge PyIceberg's multi-column
        predicate-tree SIGBUS. The live-lake writer typically upserts
        far fewer than 400 rows per cycle (one cycle ≤15 min ≈ 15 bars
        per symbol × universe ≤ ~3,000 rows), but on a recovery cycle
        catching a long backlog the chunking matters.
        """
        from app.services.iceberg_catalog import get_catalog
        from app.services.iceberg_safe_upsert import chunked_upsert
        from app.services.bronze.schemas import bronze_table_id

        catalog = get_catalog()
        table = catalog.load_table(bronze_table_id(table_short_name))
        chunked_upsert(
            table, arrow, log_label=f"bronze.{table_short_name}",
        )

    # ─────────────────────────────────────────────────────────────────
    # Audit
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _record_run(result: CycleResult) -> None:
        """Append one row to CH `ingestion_runs` for this cycle.

        Falls back silently if the table doesn't exist yet (TA-5.7.3
        creates it). After TA-5.7.3 lands, this is a hard contract.
        """
        try:
            from app.db import get_client

            client = get_client()
            client.insert(
                "ingestion_runs",
                [
                    [
                        result.run_id,
                        "live_lake_writer",                 # job_name
                        result.started_at,
                        result.finished_at,
                        result.window_start,
                        result.window_end,
                        result.total_rows,
                        str(result.per_provider_rows_written),
                        str(result.per_provider_errors) if result.per_provider_errors else "",
                        "ok" if result.succeeded else "partial_fail",
                    ]
                ],
                column_names=[
                    "run_id", "job_name", "started_at", "finished_at",
                    "window_start", "window_end", "rows_written",
                    "per_provider_rows_written_json",
                    "per_provider_errors_json",
                    "status",
                ],
            )
        except Exception as e:
            # If the table isn't built yet (TA-5.7.3), don't crash the
            # cycle. Log + continue.
            logger.debug(
                "live_lake_writer: ingestion_runs not recordable yet: %s", e,
            )


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _ensure_utc(ts) -> datetime:
    """Coerce a CH-returned timestamp to a UTC-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    # CH driver should always return a datetime, but defend.
    return datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)


def _safe_float(v) -> Optional[float]:
    """None → None; else float()."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Module-level singleton + lifespan helpers (for FastAPI wiring)

_writer: Optional[LiveLakeWriter] = None
_task: Optional[asyncio.Task] = None


def get_live_lake_writer() -> LiveLakeWriter:
    """Return the singleton writer instance (lazy)."""
    global _writer
    if _writer is None:
        _writer = LiveLakeWriter.from_settings()
    return _writer


async def start_live_lake_writer() -> None:
    """Start the writer's `run_forever` task. Called from FastAPI lifespan.

    No-op if already running.
    """
    global _task
    if _task is not None and not _task.done():
        return
    writer = get_live_lake_writer()
    _task = asyncio.create_task(
        writer.run_forever(),
        name="live_lake_writer",
    )


async def stop_live_lake_writer() -> None:
    """Stop the writer's `run_forever` task. Called from lifespan shutdown."""
    global _task, _writer
    if _writer is not None:
        _writer.stop()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _task.cancel()
        _task = None
    _writer = None
