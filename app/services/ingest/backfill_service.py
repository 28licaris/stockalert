"""
Backfill Service - two-path historical loader (quick + deep).

Two distinct kinds of backfill, sharing one job queue but with separate
asyncio.Semaphores so a long-running deep job cannot starve a user-facing
quick job.

  - QUICK (latency-first, default 30 days):
        Triggered on watchlist adds and symbol-page visits. Aimed at making
        a usable chart appear within seconds. Concurrency 3. Short-circuits
        if the DB already has >= `quick_coverage_ratio` of the expected bars
        in the target window.

  - DEEP (completeness-first, default 365 days):
        Triggered by the coverage sweeper or by `POST /api/backfill/deep`.
        Computes the *gap* between what is in DB and the target window,
        and fetches only that gap, chunked into windows the upstream
        provider can serve in a single call. Concurrency 1.

Provider abstraction
--------------------
This service is provider-agnostic. It accepts any `DataProvider` and a
`HistoricalDataLoader`; both follow the abstract `app.providers.base`
interface. The chunk size is also configurable so we can tune per provider
(Schwab is ~10-day windows for 1-min bars).

Idempotency
-----------
`enqueue_quick(symbol)` and `enqueue_deep(symbol)` are idempotent: if a job
of the same kind is already queued or running for that symbol, the existing
task is returned and no second one is created.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from datetime import timezone

import pandas as pd

from app.config import settings
from app.db import queries
from app.providers.base import DataProvider
from app.services.ingest.historical_loader import HistoricalDataLoader

logger = logging.getLogger(__name__)


# ---------- Status records ----------


@dataclass
class JobStatus:
    """Snapshot of a single backfill job for one (symbol, kind)."""
    state: str = "idle"            # idle | queued | running | done | error | skipped | throttled
    days: int = 0                  # requested lookback window
    bars: int = 0                  # total bars persisted across all chunks
    chunks_total: int = 0
    chunks_done: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    reason: Optional[str] = None   # human-readable note (e.g. "already covered")


@dataclass
class SymbolStatus:
    """Combined status for a symbol's quick / deep / intraday / daily / gap_fill jobs."""
    quick: JobStatus = field(default_factory=JobStatus)
    deep: JobStatus = field(default_factory=JobStatus)
    intraday: JobStatus = field(default_factory=JobStatus)
    daily: JobStatus = field(default_factory=JobStatus)
    gap_fill: JobStatus = field(default_factory=JobStatus)

    def to_dict(self) -> dict:
        return {
            "quick": self.quick.__dict__,
            "deep": self.deep.__dict__,
            "intraday": self.intraday.__dict__,
            "daily": self.daily.__dict__,
            "gap_fill": self.gap_fill.__dict__,
        }


# ---------- Now() abstraction (injectable for tests) ----------


NowFn = Callable[[], datetime]


def _real_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Backfill service ----------


class BackfillService:
    """
    Singleton orchestrator for quick + deep historical backfill. The service
    owns no persistent state of its own; ClickHouse is the source of truth
    for what has been backfilled (queried via `app.db.queries.coverage`).
    """

    # Schwab's pricehistory caps 1-minute bars at ~10 days per call. We pick a
    # slightly smaller window to leave room for clock skew and to avoid edge
    # cases at the boundary.
    DEFAULT_CHUNK_DAYS = 9

    def __init__(
        self,
        *,
        loader: Optional[HistoricalDataLoader] = None,
        provider_factory: Optional[Callable[[], DataProvider]] = None,
        quick_concurrency: int = 3,
        deep_concurrency: int = 1,
        chunk_days: int = DEFAULT_CHUNK_DAYS,
        quick_coverage_ratio: float = 0.9,
        now_fn: NowFn = _real_now,
        coverage_fn: Optional[Callable[..., Awaitable[dict]]] = None,
        flatfiles_service: Optional["object"] = None,
    ) -> None:
        # Lazy: a loader is only created on first job, so the service can
        # be constructed safely at import time even if no provider is configured.
        self._loader: Optional[HistoricalDataLoader] = loader
        self._provider_factory = provider_factory
        self._sem_quick = asyncio.Semaphore(max(1, quick_concurrency))
        self._sem_deep = asyncio.Semaphore(max(1, deep_concurrency))
        self._chunk_days = max(1, chunk_days)
        self._quick_coverage_ratio = max(0.0, min(1.0, quick_coverage_ratio))
        self._now_fn = now_fn
        # Allow tests to swap the coverage function.
        self._coverage_fn = coverage_fn or queries.coverage_async
        # Flat-files deep path. Lazily constructed on first use when Polygon
        # is the configured history provider AND POLYGON_FLATFILES_ENABLED.
        # Tests inject directly; production builds via from_settings(). Typed
        # as ``object`` to avoid a hard import (flat-files needs boto3 +
        # pyarrow, which we don't want to require for Schwab-only deploys).
        self._flatfiles_service = flatfiles_service

        # symbol -> SymbolStatus
        self._status: dict[str, SymbolStatus] = {}
        # (symbol, kind) -> asyncio.Task currently running for that pair
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._started = False
        self._lock = asyncio.Lock()

        # ---------- Throttle ----------
        # Per-(symbol, kind) cooldown: when an enqueue completes (done OR
        # skipped) we record `now`. A subsequent enqueue within the throttle
        # window returns `{state: "throttled"}` immediately without doing a
        # coverage query or provider call. Pass `force=True` to bypass.
        #
        # This is the single biggest knob for keeping the app snappy:
        # uvicorn-restart auto-enqueues, symbol-page revisits, and the daily
        # sweeper all become free no-ops within the cooldown.
        self._last_completed: dict[tuple[str, str], datetime] = {}
        self._throttle: dict[str, timedelta] = {
            "quick":    timedelta(hours=4),   # 1m refill — short cooldown so
                                              # dev clicks aren't blocked too long
            "intraday": timedelta(hours=24),  # 5m / >48d data — once daily is plenty
            "daily":    timedelta(hours=24),  # daily candles finalize at close
            "gap_fill": timedelta(hours=6),   # surgical refetch; sweeper runs 1×/day
            "deep":     timedelta(days=7),    # 1y 1m chunked job - expensive
        }

        # ---------- Background sweeper ----------
        # Populated in `start()` and torn down in `stop()`. Sweeper runs ONCE
        # per UTC day at the configured hour, NOT every 15 minutes. The
        # throttle layer handles inter-call dedup; this loop is just the
        # daily kick.
        self._sweeper_task: Optional[asyncio.Task] = None
        # UTC hour to run the daily sweep at. 06:00 UTC == 02:00 ET, after
        # extended-hours close so we operate during the quietest window.
        self._sweeper_run_hour_utc: int = 6
        self._sweeper_window_days: int = 7
        self._symbol_provider: Optional[Callable[[], list[str]]] = None

    # ----- lifecycle -----

    def set_symbol_provider(self, fn: Callable[[], list[str]]) -> None:
        """Inject the function that returns symbols to sweep for gaps."""
        self._symbol_provider = fn

    async def start(self) -> None:
        self._started = True
        logger.info(
            "BackfillService started (quick_sem=%d, deep_sem=%d, chunk_days=%d)",
            self._sem_quick._value,  # type: ignore[attr-defined]
            self._sem_deep._value,   # type: ignore[attr-defined]
            self._chunk_days,
        )
        # Kick off the periodic gap sweeper. Safe to start even if
        # `_symbol_provider` hasn't been injected yet: the loop just no-ops
        # until a provider is registered.
        if self._sweeper_task is None or self._sweeper_task.done():
            self._sweeper_task = asyncio.create_task(
                self._gap_sweeper_loop(), name="backfill_gap_sweeper",
            )

    async def stop(self) -> None:
        self._started = False
        if self._sweeper_task is not None and not self._sweeper_task.done():
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sweeper_task = None
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    def _seconds_until_next_sweep(self) -> float:
        """
        How many seconds from now until the next `_sweeper_run_hour_utc` (UTC).
        Returns a value in `(0, 86400]`. Pure function for testability.
        """
        now = self._now_fn()
        target = now.replace(hour=self._sweeper_run_hour_utc, minute=0,
                             second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return max(1.0, (target - now).total_seconds())

    async def _gap_sweeper_loop(self) -> None:
        """
        Background maintenance task. Runs ONCE per UTC day at
        `_sweeper_run_hour_utc` (default 06:00 UTC = ~02:00 ET, after
        extended-hours close so we operate during the quietest window).

        Each sweep iterates the currently-streaming symbols and enqueues a
        `gap_fill` job. The throttle layer makes per-symbol enqueues free
        no-ops when the symbol was filled recently, so this is safe to call
        even right after a restart.

        Errors are caught + logged: this loop is a maintenance task and must
        never crash the service.
        """
        while self._started:
            wait_s = self._seconds_until_next_sweep()
            logger.info(
                "gap sweeper: next sweep at %02d:00 UTC (in %.1fh)",
                self._sweeper_run_hour_utc, wait_s / 3600.0,
            )
            try:
                await asyncio.sleep(wait_s)
            except asyncio.CancelledError:
                return
            if not self._started:
                return

            try:
                provider_fn = self._symbol_provider
                symbols: list[str] = []
                if provider_fn is not None:
                    try:
                        symbols = list(provider_fn() or [])
                    except Exception as e:
                        logger.warning("gap sweeper: symbol provider failed: %s", e)
                logger.info("gap sweeper: scanning %d symbol(s) for gaps", len(symbols))
                for sym in symbols:
                    try:
                        self.enqueue_gap_fill(sym, days=self._sweeper_window_days,
                                              source="ohlcv_1m")
                    except Exception as e:
                        logger.warning("gap sweeper: enqueue %s failed: %s", sym, e)
            except Exception as e:
                logger.error("gap sweeper iteration failed: %s", e, exc_info=True)

    # ----- helpers -----

    def _loader_or_build(self) -> Optional[HistoricalDataLoader]:
        if self._loader is not None:
            return self._loader
        if self._provider_factory is None:
            try:
                # Explicitly use the history-role provider so users running with
                # STREAM_PROVIDER != HISTORY_PROVIDER backfill from the correct
                # vendor (e.g. Polygon Flat Files / REST for history, even when
                # live streaming is happening through a cheaper feed).
                from app.config import get_history_provider
                self._provider_factory = get_history_provider  # type: ignore[assignment]
            except Exception as e:
                logger.error("BackfillService: could not build provider_factory: %s", e)
                return None
        try:
            provider = self._provider_factory()
            self._loader = HistoricalDataLoader(provider)
            return self._loader
        except Exception as e:
            logger.error("BackfillService: could not initialize HistoricalDataLoader: %s", e)
            return None

    def _sym_status(self, symbol: str) -> SymbolStatus:
        sym = symbol.upper()
        st = self._status.get(sym)
        if st is None:
            st = SymbolStatus()
            self._status[sym] = st
        return st

    @staticmethod
    def _history_source_tag() -> str:
        """
        Source-column value to stamp on every backfilled bar. Falls back to
        the effective HISTORY_PROVIDER so the lake archive can route deltas
        to the right ``raw/provider=<source>/`` partition. Users may pin a
        more specific tag (e.g. ``polygon-flatfiles`` vs ``polygon``) via
        DATA_SOURCE_TAG.
        """
        return (
            (settings.data_source_tag or "").strip()
            or settings.effective_history_provider
        )

    # ----- public API -----

    async def coverage(self, symbol: str, days: int = 30) -> dict:
        """Coverage report for `symbol` over the last `days` days."""
        end = self._now_fn()
        start = end - timedelta(days=max(1, days))
        cov = await self._coverage_fn(symbol, start, end)
        # Approximate "expected" bars (RTH minutes per trading day ~ 390; assume 5/7 trading days).
        approx_trading_days = max(1.0, days * (5.0 / 7.0))
        expected = int(approx_trading_days * 390)
        ratio = (cov["bar_count"] / expected) if expected else 0.0
        earliest = cov["earliest"]
        latest = cov["latest"]
        if earliest is not None and earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)
        if latest is not None and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return {
            "symbol": cov["symbol"],
            "window_days": days,
            "start": cov["start"].isoformat(),
            "end": cov["end"].isoformat(),
            "earliest": earliest.isoformat() if earliest else None,
            "latest": latest.isoformat() if latest else None,
            "bar_count": cov["bar_count"],
            "expected_approx": expected,
            "ratio": round(ratio, 4),
        }

    def status(self, symbol: Optional[str] = None) -> dict:
        if symbol:
            return {symbol.upper(): self._sym_status(symbol).to_dict()}
        return {sym: st.to_dict() for sym, st in self._status.items()}

    def enqueue_quick(self, symbol: str, days: int = 30, *, force: bool = False) -> dict:
        return self._enqueue(symbol, days=days, kind="quick", force=force)

    def enqueue_deep(self, symbol: str, days: int = 365, *, force: bool = False) -> dict:
        return self._enqueue(symbol, days=days, kind="deep", force=force)

    def enqueue_daily(self, symbol: str, days: int = 365 * 2, *, force: bool = False) -> dict:
        """
        Fetch native daily candles for `[now - days, now]` from the provider
        and persist into `ohlcv_daily`. Schwab returns 20+ years of daily
        history in a single call, so we don't bother chunking by default.
        """
        return self._enqueue(symbol, days=days, kind="daily", force=force)

    def enqueue_intraday(self, symbol: str, days: int = 270, *, force: bool = False) -> dict:
        """
        Fetch native 5-minute candles for `[now - days, now]` and persist
        into `ohlcv_5m`. Schwab's pricehistory serves ~270 days of 5-min
        history per call, so this is the source for 5m/15m/30m/1h/4h
        queries with lookback > 48 days (the 1-min limit).
        """
        return self._enqueue(symbol, days=days, kind="intraday", force=force)

    def sweep_now(self, *, days: Optional[int] = None) -> dict:
        """
        One-shot gap sweep across the currently registered streaming symbols.

        Intended to be called at startup (or via an admin endpoint) so the user
        doesn't have to wait until the next daily 06:00 UTC sweep when gaps
        accumulate mid-day — e.g. after switching providers, after a restart
        that took longer than one bar, or after the user manually edits the
        watchlist. Per-symbol `gap_fill` throttle still applies, so repeated
        kicks within the cooldown window are free no-ops.

        Returns a dict describing what was enqueued so callers (e.g. tests,
        admin endpoints) can assert the right thing happened.
        """
        if self._symbol_provider is None:
            return {"scanned": 0, "skipped": True, "reason": "no symbol provider"}
        try:
            symbols = list(self._symbol_provider() or [])
        except Exception as e:
            logger.warning("sweep_now: symbol provider failed: %s", e)
            return {"scanned": 0, "error": str(e)}
        window = days if days is not None else self._sweeper_window_days
        results: list[dict] = []
        for sym in symbols:
            try:
                res = self.enqueue_gap_fill(sym, days=window, source="ohlcv_1m")
                results.append({"symbol": sym, "state": res.get("state")})
            except Exception as e:
                logger.warning("sweep_now: enqueue %s failed: %s", sym, e)
                results.append({"symbol": sym, "state": "error", "error": str(e)})
        logger.info(
            "sweep_now: enqueued %d gap-fill job(s) over %dd window",
            len(results), window,
        )
        return {"scanned": len(results), "window_days": window, "results": results}

    def enqueue_gap_fill(self, symbol: str, days: int = 30, *,
                        source: str = "ohlcv_1m",
                        force: bool = False) -> dict:
        """
        Detect within-session gaps in `[now - days, now]` for the given source
        table (`ohlcv_1m` or `ohlcv_5m`) and re-fetch each gap range from the
        provider. Unlike `enqueue_quick`, this path does NOT short-circuit on
        coverage ratio: even a 99%-dense window will be re-checked because the
        whole point is to find the 1% of holes inside it.

        Schwab's pricehistory honors `startDate`/`endDate` for sub-day ranges,
        so each gap is fetched as a narrow window. To avoid hammering the API,
        adjacent gaps within `GAP_MERGE_MINUTES` are merged into a single fetch.
        """
        return self._enqueue(symbol, days=days, kind="gap_fill", force=force, source=source)

    # ----- internals -----

    def _enqueue(self, symbol: str, *, days: int, kind: str,
                 force: bool = False, **runner_kwargs) -> dict:
        sym = symbol.upper().strip()
        if not sym:
            return {"symbol": "", "kind": kind, "state": "error", "error": "empty symbol"}
        days = max(1, int(days))

        # Dedup: if a task of the same kind is in flight, return its current status.
        key = (sym, kind)
        existing = self._tasks.get(key)
        if existing and not existing.done():
            st = self._sym_status(sym)
            job_map = {"quick": st.quick, "deep": st.deep, "intraday": st.intraday,
                       "daily": st.daily, "gap_fill": st.gap_fill}
            job = job_map.get(kind, st.quick)
            return {"symbol": sym, "kind": kind, "state": job.state, "reason": "already running"}

        # Throttle: if this (symbol, kind) completed recently AND the caller
        # didn't pass force=True, return immediately without doing ANY work.
        # This makes uvicorn-restart / symbol-page-revisit / sweeper-tick free.
        if not force:
            cooldown = self._throttle.get(kind)
            last = self._last_completed.get(key)
            if cooldown is not None and last is not None:
                elapsed = self._now_fn() - last
                if elapsed < cooldown:
                    remaining = cooldown - elapsed
                    mins_remaining = int(remaining.total_seconds() // 60)
                    return {
                        "symbol": sym, "kind": kind, "state": "throttled",
                        "reason": f"ran {int(elapsed.total_seconds() // 60)} min ago; "
                                  f"cooldown {int(cooldown.total_seconds() // 60)} min "
                                  f"({mins_remaining} min remaining)",
                    }

        # Mark queued immediately so callers see it before the event loop tick.
        st = self._sym_status(sym)
        job = {
            "quick": st.quick,
            "deep": st.deep,
            "intraday": st.intraday,
            "daily": st.daily,
            "gap_fill": st.gap_fill,
        }.get(kind)
        if job is None:
            return {"symbol": sym, "kind": kind, "state": "error",
                    "error": f"unknown kind: {kind!r}"}
        job.state = "queued"
        job.days = days
        job.error = None
        job.reason = None

        runner = {
            "quick": self._run_quick,
            "deep": self._run_deep,
            "intraday": self._run_intraday,
            "daily": self._run_daily,
            "gap_fill": self._run_gap_fill,
        }[kind]
        task = asyncio.create_task(runner(sym, days, **runner_kwargs),
                                    name=f"backfill_{kind}_{sym}")
        self._tasks[key] = task

        def _on_done(_t: asyncio.Task) -> None:
            # Drop the in-flight reference and, if the job actually completed
            # successfully (done OR coverage-skipped), record the timestamp so
            # the throttle layer can short-circuit subsequent enqueues.
            # We DO NOT record on error/throttled so transient failures
            # don't block retries.
            self._tasks.pop(key, None)
            try:
                st = self._sym_status(sym)
                job_map = {"quick": st.quick, "deep": st.deep,
                           "intraday": st.intraday, "daily": st.daily,
                           "gap_fill": st.gap_fill}
                job = job_map.get(kind)
                if job is not None and job.state in ("done", "skipped"):
                    self._last_completed[key] = self._now_fn()
            except Exception:
                pass

        task.add_done_callback(_on_done)

        return {"symbol": sym, "kind": kind, "state": "queued", "days": days}

    async def _run_quick(self, symbol: str, days: int) -> None:
        async with self._sem_quick:
            await self._execute_window(symbol, days=days, kind="quick")

    async def _run_deep(self, symbol: str, days: int) -> None:
        async with self._sem_deep:
            await self._execute_deep(symbol, days=days)

    async def _run_daily(self, symbol: str, days: int) -> None:
        # Reuse the deep semaphore so we don't pound the provider with parallel
        # long-history calls (Schwab daily is a single request but the loader
        # may compete with deep 1m chunks).
        async with self._sem_deep:
            await self._execute_daily(symbol, days=days)

    async def _run_intraday(self, symbol: str, days: int) -> None:
        # Reuse deep semaphore - 5m fetches are long-running too.
        async with self._sem_deep:
            await self._execute_intraday(symbol, days=days)

    async def _run_gap_fill(self, symbol: str, days: int, *,
                            source: str = "ohlcv_1m") -> None:
        # Quick-semaphore: individual gap fetches are small windows.
        async with self._sem_quick:
            await self._execute_gap_fill(symbol, days=days, source=source)

    async def _execute_window(self, symbol: str, *, days: int, kind: str) -> None:
        """Single-window fetch with coverage short-circuit. Used by the quick path."""
        st = self._sym_status(symbol)
        job = st.quick if kind == "quick" else st.deep
        job.state = "running"
        job.started_at = self._now_fn().isoformat()
        job.bars = 0
        job.chunks_total = 1
        job.chunks_done = 0

        try:
            cov = await self.coverage(symbol, days=days)
            if cov["ratio"] >= self._quick_coverage_ratio:
                job.state = "skipped"
                job.reason = f"already {int(cov['ratio'] * 100)}% covered"
                job.bars = cov["bar_count"]
                job.finished_at = self._now_fn().isoformat()
                logger.info(
                    "Backfill %s %s: skipped (%d bars, ratio=%.2f)",
                    kind, symbol, cov["bar_count"], cov["ratio"],
                )
                return

            bars = await self._fetch_and_persist(symbol, days_back=days)
            job.bars = bars
            job.chunks_done = 1
            job.state = "done"
            job.finished_at = self._now_fn().isoformat()
            logger.info("Backfill %s %s: done (%d bars over %dd)", kind, symbol, bars, days)
        except Exception as e:
            job.state = "error"
            job.error = str(e)
            job.finished_at = self._now_fn().isoformat()
            logger.error("Backfill %s %s failed: %s", kind, symbol, e, exc_info=True)

    @staticmethod
    def _should_use_flatfiles_for_deep() -> bool:
        """
        True when the deep backfill should route through Polygon Flat Files
        instead of REST. Gated on three conditions:

          1. The effective history provider is ``polygon`` (so we have an
             entitlement to flat files in the first place).
          2. ``POLYGON_FLATFILES_ENABLED`` is set (opt-in; lets users on
             Polygon plans WITHOUT flat files keep using REST without
             config gymnastics).
          3. Both S3 keys are present (otherwise the lazy ``from_settings()``
             would raise on first use).

        Static so tests can patch ``app.config.settings`` cheaply.
        """
        if settings.effective_history_provider != "polygon":
            return False
        if not getattr(settings, "polygon_flatfiles_enabled", False):
            return False
        return bool(
            (settings.polygon_s3_access_key_id or "").strip()
            and (settings.polygon_s3_secret_access_key or "").strip()
        )

    def _get_flatfiles_service(self):
        """Lazy-build the FlatFilesBackfillService. Returns ``None`` and
        logs (does NOT raise) if construction fails — callers must then
        fall back to the REST deep path so a misconfigured flat-files
        install never silently breaks backfill."""
        if self._flatfiles_service is not None:
            return self._flatfiles_service
        try:
            from app.services.ingest.flatfiles_backfill import FlatFilesBackfillService
            self._flatfiles_service = FlatFilesBackfillService.from_settings()
            return self._flatfiles_service
        except Exception as e:
            logger.warning(
                "BackfillService: flat-files unavailable, falling back to REST: %s",
                e,
            )
            return None

    async def _execute_deep(self, symbol: str, *, days: int) -> None:
        """Gap-aware, chunked fetch. Used by the deep path."""
        # Provider-aware dispatch: when Polygon Flat Files is the configured
        # history source, route deep backfill through the bulk S3 path
        # (~one request per trading day, fetches every symbol simultaneously
        # at the wire level). REST stays as the fallback for any failure or
        # for non-Polygon history providers.
        if self._should_use_flatfiles_for_deep():
            ff_svc = self._get_flatfiles_service()
            if ff_svc is not None:
                await self._execute_deep_via_flatfiles(symbol, days=days, ff_svc=ff_svc)
                return
            # Flat-files build failed; fall through to REST below.

        st = self._sym_status(symbol)
        job = st.deep
        job.state = "running"
        job.started_at = self._now_fn().isoformat()
        job.bars = 0
        job.chunks_done = 0

        try:
            now = self._now_fn()
            target_start = now - timedelta(days=days)

            # Compute which window(s) we actually need to fetch. If DB already
            # has bars stretching back to before `target_start`, the deep job is
            # effectively a no-op. Otherwise we fill the gap from the earliest
            # bar we have backwards (or the full window if DB is empty).
            cov_full = await self._coverage_fn(symbol, target_start, now)
            existing_earliest = cov_full["earliest"]
            # ClickHouse returns naive datetimes; coerce to UTC for comparisons.
            if existing_earliest is not None and existing_earliest.tzinfo is None:
                existing_earliest = existing_earliest.replace(tzinfo=timezone.utc)

            # The gap window is [target_start, gap_end_exclusive). If DB is empty
            # we fetch the entire window; otherwise we only need everything older
            # than the earliest existing bar.
            if existing_earliest is None:
                fetch_end = now
            else:
                fetch_end = existing_earliest
                if fetch_end <= target_start:
                    job.state = "skipped"
                    job.reason = "no gap before target window"
                    job.bars = cov_full["bar_count"]
                    job.finished_at = self._now_fn().isoformat()
                    logger.info(
                        "Backfill deep %s: skipped (earliest=%s, target_start=%s)",
                        symbol, existing_earliest, target_start,
                    )
                    return

            # Chunk [target_start, fetch_end] into <=chunk_days windows.
            chunks: list[tuple[datetime, datetime]] = []
            cursor_end = fetch_end
            while cursor_end > target_start:
                cursor_start = max(target_start, cursor_end - timedelta(days=self._chunk_days))
                chunks.append((cursor_start, cursor_end))
                cursor_end = cursor_start
            job.chunks_total = len(chunks)

            total_bars = 0
            for chunk_start, chunk_end in chunks:
                bars = await self._fetch_and_persist_range(symbol, chunk_start, chunk_end)
                total_bars += bars
                job.bars = total_bars
                job.chunks_done += 1
                logger.info(
                    "Backfill deep %s: chunk %d/%d %s..%s -> %d bars",
                    symbol, job.chunks_done, job.chunks_total,
                    chunk_start.date(), chunk_end.date(), bars,
                )

            job.state = "done"
            job.finished_at = self._now_fn().isoformat()
            logger.info(
                "Backfill deep %s: done (chunks=%d, bars=%d, %dd target)",
                symbol, job.chunks_done, total_bars, days,
            )
        except Exception as e:
            job.state = "error"
            job.error = str(e)
            job.finished_at = self._now_fn().isoformat()
            logger.error("Backfill deep %s failed: %s", symbol, e, exc_info=True)

    async def _execute_deep_via_flatfiles(
        self, symbol: str, *, days: int, ff_svc,
    ) -> None:
        """
        Deep backfill via Polygon Flat Files.

        Strategy diverges from the REST path in three important ways:

          - We don't chunk by ``chunk_days``. Each S3 GET fetches an entire
            trading day for every US ticker; chunking would just multiply
            the number of round trips for no benefit.
          - We don't pre-compute the gap. The flat-files service walks
            ``available_dates`` itself (which handles weekends/holidays),
            and ClickHouse's ReplacingMergeTree(version) makes re-ingesting
            already-covered days a safe no-op.
          - Status accounting maps the per-day result counters back onto
            the existing ``JobStatus`` shape so the UI / status endpoint
            doesn't need to learn a second progress model.
        """
        st = self._sym_status(symbol)
        job = st.deep
        job.state = "running"
        job.started_at = self._now_fn().isoformat()
        job.bars = 0
        job.chunks_done = 0
        job.chunks_total = 0

        try:
            now = self._now_fn()
            target_start = now - timedelta(days=days)
            # Use a coverage check identical to the REST path so we still
            # short-circuit when nothing genuinely new is needed. With
            # flat-files this matters less (dedup is free) but it keeps the
            # status UI consistent between providers.
            cov_full = await self._coverage_fn(symbol, target_start, now)
            existing_earliest = cov_full["earliest"]
            if existing_earliest is not None and existing_earliest.tzinfo is None:
                existing_earliest = existing_earliest.replace(tzinfo=timezone.utc)
            if existing_earliest is None:
                fetch_end = now
            else:
                fetch_end = existing_earliest
                if fetch_end <= target_start:
                    job.state = "skipped"
                    job.reason = "no gap before target window"
                    job.bars = cov_full["bar_count"]
                    job.finished_at = self._now_fn().isoformat()
                    logger.info(
                        "Backfill deep %s (flat-files): skipped (earliest=%s)",
                        symbol, existing_earliest,
                    )
                    return

            result = await ff_svc.backfill_range(
                [symbol],
                target_start.date(),
                fetch_end.date(),
                kind="minute",
            )

            job.chunks_total = result.days_listed
            # Treat any non-errored day (ok / filtered / missing / skipped)
            # as "made progress" so the UI shows steady forward motion even
            # on weekends / for symbols with sparse flat-file coverage.
            job.chunks_done = (
                result.days_listed - result.days_errored
            )
            job.bars = result.bars_persisted
            if result.days_errored > 0:
                job.state = "error"
                job.error = f"{result.days_errored} day(s) failed"
            else:
                job.state = "done"
            job.reason = (
                f"flat-files: {result.days_ok} ok / "
                f"{result.days_filtered} filtered / "
                f"{result.days_missing} missing / "
                f"{result.bars_persisted} bars over {days}d"
            )
            job.finished_at = self._now_fn().isoformat()
            logger.info(
                "Backfill deep %s (flat-files): %s (bars=%d, days_ok=%d, "
                "filtered=%d, missing=%d, errored=%d)",
                symbol, job.state, result.bars_persisted,
                result.days_ok, result.days_filtered,
                result.days_missing, result.days_errored,
            )
        except Exception as e:
            job.state = "error"
            job.error = str(e)
            job.finished_at = self._now_fn().isoformat()
            logger.error(
                "Backfill deep %s (flat-files) failed: %s",
                symbol, e, exc_info=True,
            )

    async def _execute_intraday(self, symbol: str, *, days: int) -> None:
        """
        Fetch native 5-minute bars from the provider for the requested window
        and persist into `ohlcv_5m`. Schwab serves ~270d of 5m per call so the
        single-window path is sufficient - we do NOT chunk by default.
        Coverage short-circuit: skip if the table already covers `target_start`.
        """
        st = self._sym_status(symbol)
        job = st.intraday
        job.state = "running"
        job.started_at = self._now_fn().isoformat()
        job.bars = 0
        job.chunks_total = 1
        job.chunks_done = 0

        try:
            now = self._now_fn()
            target_start = now - timedelta(days=days)
            cov = await asyncio.to_thread(queries.coverage_5m, symbol, target_start, now)
            earliest = cov["earliest"]
            if earliest is not None and earliest.tzinfo is None:
                earliest = earliest.replace(tzinfo=timezone.utc)
            # If the existing 5m table already covers (close to) the requested
            # start, skip. We give a 2-day grace so we don't refetch a 268-day
            # range just because the existing range starts at 270d minus 2.
            if earliest is not None and earliest <= target_start + timedelta(days=2):
                job.state = "skipped"
                job.reason = f"already covers {cov['bar_count']} bars"
                job.bars = cov["bar_count"]
                job.finished_at = self._now_fn().isoformat()
                logger.info(
                    "Backfill intraday %s: skipped (%d bars from %s)",
                    symbol, cov["bar_count"], earliest,
                )
                return

            bars = await self._fetch_and_persist_5m(symbol, target_start, now)
            job.bars = bars
            job.chunks_done = 1
            job.state = "done"
            job.finished_at = self._now_fn().isoformat()
            logger.info("Backfill intraday %s: done (%d bars over %dd)", symbol, bars, days)
        except Exception as e:
            job.state = "error"
            job.error = str(e)
            job.finished_at = self._now_fn().isoformat()
            logger.error("Backfill intraday %s failed: %s", symbol, e, exc_info=True)

    async def _execute_daily(self, symbol: str, *, days: int) -> None:
        """
        Fetch native daily bars from the provider for the requested window
        and persist into `ohlcv_daily`. Coverage short-circuit: if the DB
        already has bars going back to before `target_start`, skip.
        """
        st = self._sym_status(symbol)
        job = st.daily
        job.state = "running"
        job.started_at = self._now_fn().isoformat()
        job.bars = 0
        job.chunks_total = 1
        job.chunks_done = 0

        try:
            now = self._now_fn()
            target_start = now - timedelta(days=days)
            cov = await asyncio.to_thread(queries.daily_coverage, symbol, target_start, now)
            cov_earliest = cov["earliest"]
            if cov_earliest is not None and cov_earliest.tzinfo is None:
                cov_earliest = cov_earliest.replace(tzinfo=timezone.utc)
            if cov_earliest is not None and cov_earliest <= target_start + timedelta(days=2):
                # Already covers target. Skip.
                job.state = "skipped"
                job.reason = f"already covers {cov['bar_count']} days"
                job.bars = cov["bar_count"]
                job.finished_at = self._now_fn().isoformat()
                logger.info(
                    "Backfill daily %s: skipped (%d bars from %s)",
                    symbol, cov["bar_count"], cov_earliest,
                )
                return

            bars = await self._fetch_and_persist_daily(symbol, target_start, now)
            job.bars = bars
            job.chunks_done = 1
            job.state = "done"
            job.finished_at = self._now_fn().isoformat()
            logger.info("Backfill daily %s: done (%d bars over %dd)", symbol, bars, days)
        except Exception as e:
            job.state = "error"
            job.error = str(e)
            job.finished_at = self._now_fn().isoformat()
            logger.error("Backfill daily %s failed: %s", symbol, e, exc_info=True)

    # ---------- gap-fill ----------

    # Adjacent gaps within this many minutes are merged into a single provider
    # fetch to avoid per-gap API overhead. Bumping this up trades extra bars
    # re-downloaded for fewer round trips.
    GAP_MERGE_MINUTES = 30
    # Hard cap on number of distinct provider fetches per gap-fill job. Keeps a
    # symbol with many small holes from blowing through provider rate limits.
    # When the number of merged ranges exceeds this, the **most recent** ranges
    # are kept — the user just looked at the latest chart and clicked "Fill
    # gaps", so we prioritize fresh holes over historical noise from a
    # previous provider's session boundaries.
    GAP_FETCH_LIMIT = 60
    # Pad each gap window by this many minutes on each side so any bars exactly
    # at the boundaries are returned (Schwab is inclusive but defensive).
    GAP_PAD_MINUTES = 5

    @staticmethod
    def _merge_gap_ranges(gaps: list[dict], merge_minutes: int) -> list[tuple[datetime, datetime]]:
        """
        Collapse adjacent gaps whose endpoints are within `merge_minutes` of each
        other into a single (start, end) range. Each input row is
        {prev_ts, next_ts, missing}. The returned ranges are the OUTER bounds we
        want to refetch from the provider.
        """
        if not gaps:
            return []
        ranges: list[tuple[datetime, datetime]] = []
        cur_start = gaps[0]["prev_ts"]
        cur_end = gaps[0]["next_ts"]
        for g in gaps[1:]:
            if (g["prev_ts"] - cur_end).total_seconds() / 60 <= merge_minutes:
                cur_end = g["next_ts"]
            else:
                ranges.append((cur_start, cur_end))
                cur_start, cur_end = g["prev_ts"], g["next_ts"]
        ranges.append((cur_start, cur_end))
        return ranges

    async def _execute_gap_fill(self, symbol: str, *, days: int, source: str) -> None:
        """
        Detect within-session gaps in [now - days, now] and refetch each gap
        range from the provider. Stores into the same source table.
        """
        if source not in ("ohlcv_1m", "ohlcv_5m"):
            raise ValueError(f"Unsupported gap-fill source: {source!r}")
        st = self._sym_status(symbol)
        job = st.gap_fill
        job.state = "running"
        job.started_at = self._now_fn().isoformat()
        job.bars = 0
        job.chunks_total = 0
        job.chunks_done = 0
        job.reason = None

        try:
            now = self._now_fn()
            start = now - timedelta(days=days)
            gaps = await queries.find_intraday_gaps_async(
                symbol, start, now, source_table=source,
            )
            if not gaps:
                job.state = "skipped"
                job.reason = "no within-session gaps detected"
                job.finished_at = self._now_fn().isoformat()
                logger.info("Backfill gap_fill %s: skipped (no gaps in %dd window)", symbol, days)
                return

            # Merge adjacent gaps + cap the number of provider fetches.
            # The merger returns ranges in chronological (ASC) order. When we
            # exceed the per-symbol fetch budget, keep the **newest** ranges
            # so today's holes always get a chance to be filled even when a
            # symbol carries a long tail of old historical gaps. We also walk
            # the kept ranges newest-first so the most user-visible bars are
            # persisted before any provider hiccup or rate-limit kicks in.
            ranges = self._merge_gap_ranges(gaps, self.GAP_MERGE_MINUTES)
            if len(ranges) > self.GAP_FETCH_LIMIT:
                logger.warning(
                    "Backfill gap_fill %s: %d gap ranges > limit %d; "
                    "keeping the %d most recent",
                    symbol, len(ranges), self.GAP_FETCH_LIMIT, self.GAP_FETCH_LIMIT,
                )
                ranges = ranges[-self.GAP_FETCH_LIMIT:]

            job.chunks_total = len(ranges)
            total_missing = sum(g["missing"] for g in gaps)
            job.reason = f"{len(gaps)} gap(s), {total_missing} bars missing"

            pad = timedelta(minutes=self.GAP_PAD_MINUTES)
            persisted = 0
            for r_start, r_end in reversed(ranges):
                try:
                    fetched = await self._fetch_gap_range(
                        symbol, r_start - pad, r_end + pad, source=source,
                    )
                    persisted += fetched
                    job.chunks_done += 1
                    job.bars = persisted
                except Exception as e:
                    logger.warning(
                        "Backfill gap_fill %s: fetch %s..%s failed: %s",
                        symbol, r_start, r_end, e,
                    )

            # Re-measure gaps so the user sees the remainder (if Schwab couldn't
            # produce some bars, they're outside the provider's history).
            remaining = await queries.find_intraday_gaps_async(
                symbol, start, now, source_table=source,
            )
            remaining_missing = sum(g["missing"] for g in remaining)
            job.reason = (
                f"filled {persisted} bars across {len(ranges)} window(s); "
                f"{len(remaining)} gap(s) remain ({remaining_missing} bars)"
            )
            job.state = "done"
            job.finished_at = self._now_fn().isoformat()
            logger.info(
                "Backfill gap_fill %s: done (persisted=%d, ranges=%d, remaining_gaps=%d)",
                symbol, persisted, len(ranges), len(remaining),
            )
        except Exception as e:
            job.state = "error"
            job.error = str(e)
            job.finished_at = self._now_fn().isoformat()
            logger.error("Backfill gap_fill %s failed: %s", symbol, e, exc_info=True)

    async def _fetch_gap_range(
        self, symbol: str, start: datetime, end: datetime, *, source: str,
    ) -> int:
        """Fetch a single gap window from the provider and persist into `source`."""
        if source == "ohlcv_1m":
            return await self._fetch_and_persist_range(symbol, start, end)
        # ohlcv_5m path: ask provider for 5m bars and persist into the 5m table.
        return await self._fetch_and_persist_5m(symbol, start, end)

    async def _fetch_and_persist(self, symbol: str, days_back: int) -> int:
        """Quick-path fetch: pull 1-min bars for [now-days, now] and persist."""
        end = self._now_fn()
        start = end - timedelta(days=days_back)
        return await self._fetch_and_persist_range(symbol, start, end)

    async def _fetch_and_persist_range(self, symbol: str, start: datetime, end: datetime) -> int:
        """
        Fetch directly from the provider over `[start, end]` and persist via
        `queries.insert_bars_batch_async`. Bypasses `HistoricalDataLoader`'s
        DB-first short-circuit so backfill always reaches the provider.
        Returns the number of bars written.
        """
        loader = self._loader_or_build()
        if loader is None:
            raise RuntimeError("HistoricalDataLoader is not available")

        df = await loader._fetch_from_provider(symbol, start, end)  # type: ignore[attr-defined]
        if df is None or df.empty:
            logger.info("Backfill %s: provider returned 0 bars for %s..%s", symbol, start, end)
            return 0

        await self._persist(symbol, df)
        return int(len(df))

    async def _fetch_and_persist_5m(self, symbol: str, start: datetime, end: datetime) -> int:
        """Fetch 5-minute bars from the provider and persist to `ohlcv_5m`."""
        loader = self._loader_or_build()
        if loader is None:
            raise RuntimeError("HistoricalDataLoader is not available")

        try:
            df = await asyncio.wait_for(
                loader.provider.historical_df(symbol, start, end, timeframe="5m"),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("5m fetch timed out after 90s")
        if df is None or df.empty:
            logger.info("Backfill intraday %s: provider returned 0 bars", symbol)
            return 0
        await self._persist_5m(symbol, df)
        return int(len(df))

    async def _persist_5m(self, symbol: str, df: pd.DataFrame) -> None:
        """Insert provider-fetched 5-minute bars into `ohlcv_5m`."""
        src = self._history_source_tag()
        records: list[dict] = []
        for ts, row in df.iterrows():
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            records.append({
                "symbol": symbol.upper(),
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "vwap": float(row.get("vwap", 0) or 0),
                "trade_count": int(row.get("trade_count", 0) or 0),
                "source": src,
            })
        BATCH = 1000
        for i in range(0, len(records), BATCH):
            await queries.insert_5m_bars_batch_async(records[i : i + BATCH])
        logger.info("Backfill intraday %s: persisted %d bars", symbol, len(records))

    async def _fetch_and_persist_daily(self, symbol: str, start: datetime, end: datetime) -> int:
        """Fetch daily bars from the provider and persist to `ohlcv_daily`."""
        loader = self._loader_or_build()
        if loader is None:
            raise RuntimeError("HistoricalDataLoader is not available")

        # The provider's historical_df accepts timeframe='1d' or 'day'.
        try:
            df = await asyncio.wait_for(
                loader.provider.historical_df(symbol, start, end, timeframe="1d"),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("daily fetch timed out after 60s")
        if df is None or df.empty:
            logger.info("Backfill daily %s: provider returned 0 bars", symbol)
            return 0

        await self._persist_daily(symbol, df)
        return int(len(df))

    async def _persist_daily(self, symbol: str, df: pd.DataFrame) -> None:
        """Insert provider-fetched daily bars into ClickHouse `ohlcv_daily`."""
        src = self._history_source_tag()
        records: list[dict] = []
        for ts, row in df.iterrows():
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            records.append({
                "symbol": symbol.upper(),
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "source": src,
            })
        BATCH = 1000
        for i in range(0, len(records), BATCH):
            await queries.insert_daily_bars_batch_async(records[i : i + BATCH])
        logger.info("Backfill daily %s: persisted %d bars", symbol, len(records))

    async def _persist(self, symbol: str, df: pd.DataFrame) -> None:
        """Insert provider-fetched 1-min bars into ClickHouse, deduped on insert."""
        src = self._history_source_tag()
        records: list[dict] = []
        for ts, row in df.iterrows():
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            records.append(
                {
                    "symbol": symbol.upper(),
                    "timestamp": ts,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "vwap": float(row.get("vwap", 0) or 0),
                    "trade_count": int(row.get("trade_count", 0) or 0),
                    "source": src,
                }
            )
        # Insert in 1k-row chunks to keep memory bounded and writes responsive.
        BATCH = 1000
        for i in range(0, len(records), BATCH):
            await queries.insert_bars_batch_async(records[i : i + BATCH])
        logger.info("Backfill %s: persisted %d bars", symbol, len(records))


# Singleton (constructed once per process; safe to import early because the
# provider is built lazily on first job).
backfill_service = BackfillService(
    quick_concurrency=int(getattr(settings, "quick_backfill_concurrency", 3) or 3),
    deep_concurrency=int(getattr(settings, "deep_backfill_concurrency", 1) or 1),
)
