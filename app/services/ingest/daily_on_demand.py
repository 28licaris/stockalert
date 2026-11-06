"""On-demand daily history: a user picks a ticker we do not hold; we fetch and store it.

Two steps, split on purpose so the user gets an honest answer fast:

1. :meth:`DailyDemand.check` (synchronous, a second or two): which of the tickers do
   we already hold enough history for, which are brand new, and which does Schwab not
   know at all? A typo is rejected *before anything is saved*.
2. :meth:`DailyDemand.schedule` (background thread): fetch the missing history with
   :func:`app.services.ingest.schwab_daily.ensure_daily_history`, which writes the lake
   first and ClickHouse second. The UI shows "loading price history" until it lands.

Guard rails (this is reachable by any signed-in user, and every fetch spends the one
shared Schwab token's request budget):

* at most ``MAX_NEW_PER_REQUEST`` brand-new tickers per request;
* a concurrency cap on background fetches, and in-flight de-duplication per symbol
  (in ``ensure_daily_history``);
* a 24 h memory of tickers Schwab does not know, so a typo cannot be retried into a
  request loop;
* a source outage (no token, Schwab down) raises :class:`DailyDataUnavailable` — it is
  never mistaken for "unknown ticker" and never swallowed.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.ingest.schwab_daily import (
    DEFAULT_MIN_SESSIONS,
    ClickHouseDailyStore,
    DailyStore,
    build_schwab_provider,
    ensure_daily_history,
    fetch_daily_bars,
    valid_ticker,
)

logger = logging.getLogger(__name__)

MAX_NEW_PER_REQUEST = 8
MAX_CONCURRENT_FETCHES = 2
UNKNOWN_TTL_S = 24 * 3600
_EXISTS_WINDOW_DAYS = 10


class DailyDataUnavailable(RuntimeError):
    """The market-data source cannot be reached right now (token expired, Schwab down)."""


class TooManyNewTickers(ValueError):
    """More brand-new tickers in one request than we are willing to fetch at once."""


@dataclass(frozen=True)
class DemandCheck:
    covered: tuple[str, ...] = ()       # enough history already stored
    to_fetch: tuple[str, ...] = ()      # known to Schwab; history is missing and will be fetched
    unknown: tuple[str, ...] = field(default=())  # not a ticker Schwab knows (or malformed)


def _default_exists(symbols: Sequence[str]) -> dict[str, bool]:
    """Ask Schwab whether it has ANY price history for each symbol (sync wrapper)."""
    try:
        provider = build_schwab_provider()
    except RuntimeError as exc:
        raise DailyDataUnavailable(str(exc)) from exc

    async def go() -> dict[str, bool]:
        today = date.today()
        out: dict[str, bool] = {}
        for s in symbols:
            _rows, status = await fetch_daily_bars(provider, s, today - timedelta(days=_EXISTS_WINDOW_DAYS), today)
            out[s] = status != "empty"
        return out

    try:
        return asyncio.run(go())
    except Exception as exc:  # noqa: BLE001 — a source outage, reported as such
        raise DailyDataUnavailable(f"price source unavailable: {type(exc).__name__}: {str(exc)[:120]}") from exc


def _default_background(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="daily_on_demand", daemon=True).start()


class DailyDemand:
    def __init__(
        self,
        *,
        store: DailyStore | None = None,
        exists: Callable[[Sequence[str]], dict[str, bool]] = _default_exists,
        background: Callable[[Callable[[], None]], None] = _default_background,
        ensure: Callable[..., object] | None = None,
        min_sessions: int = DEFAULT_MIN_SESSIONS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._exists = exists
        self._background = background
        self._ensure = ensure
        self._min_sessions = min_sessions
        self._clock = clock
        self._unknown_until: dict[str, float] = {}
        self._gate = threading.BoundedSemaphore(MAX_CONCURRENT_FETCHES)
        self._lock = threading.Lock()

    def _store_(self) -> DailyStore:
        if self._store is None:
            self._store = ClickHouseDailyStore()
        return self._store

    # ── step 1 ────────────────────────────────────────────────────────

    def check(self, symbols: Sequence[str]) -> DemandCheck:
        syms = list(dict.fromkeys((s or "").strip().upper() for s in symbols if (s or "").strip()))
        unknown = [s for s in syms if not valid_ticker(s)]
        todo = [s for s in syms if s not in unknown]
        cov = self._store_().coverage(todo)

        covered = [s for s in todo if (cov.get(s) and cov[s].sessions >= self._min_sessions)]
        partial = [s for s in todo if s not in covered and cov.get(s) and cov[s].sessions > 0]
        fresh = [s for s in todo if s not in covered and s not in partial]  # nothing stored at all

        now = self._clock()
        with self._lock:
            remembered = [s for s in fresh if self._unknown_until.get(s, 0) > now]
        unknown += remembered
        to_check = [s for s in fresh if s not in remembered]

        if len(to_check) > MAX_NEW_PER_REQUEST:
            raise TooManyNewTickers(
                f"Add at most {MAX_NEW_PER_REQUEST} brand-new tickers at a time "
                f"(you added {len(to_check)}); the rest can follow in a second theme."
            )

        known: list[str] = []
        if to_check:
            verdict = self._exists(to_check)  # may raise DailyDataUnavailable
            for s in to_check:
                if verdict.get(s):
                    known.append(s)
                else:
                    unknown.append(s)
                    self._remember_unknown(s)

        logger.info("daily_on_demand.check: covered=%s partial=%s new_known=%s unknown=%s",
                    covered, partial, known, unknown)
        return DemandCheck(covered=tuple(covered), to_fetch=tuple(partial + known), unknown=tuple(unknown))

    def _remember_unknown(self, symbol: str) -> None:
        with self._lock:
            self._unknown_until[symbol] = self._clock() + UNKNOWN_TTL_S

    # ── step 2 ────────────────────────────────────────────────────────

    def schedule(self, symbols: Sequence[str]) -> None:
        """Fetch and store history for ``symbols`` in the background. Never raises."""
        syms = list(symbols)
        if not syms:
            return

        def job() -> None:
            with self._gate:
                try:
                    runner = self._ensure or ensure_daily_history
                    results = asyncio.run(runner(syms))
                except Exception:  # noqa: BLE001 — a background job must report, not die silently
                    logger.exception("daily_on_demand: background fetch failed for %s", syms)
                    return
            for r in results:
                if r.status == "empty":
                    self._remember_unknown(r.symbol)
                if not r.ok:
                    logger.warning("daily_on_demand: %s -> %s (%s)", r.symbol, r.status, r.detail)

        self._background(job)


_default: DailyDemand | None = None
_default_lock = threading.Lock()


def default_demand() -> DailyDemand:
    global _default
    with _default_lock:
        if _default is None:
            _default = DailyDemand()
        return _default
