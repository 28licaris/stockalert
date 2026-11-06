"""Schwab REST daily history → lake (``equities.schwab_daily_raw``) then ClickHouse (``ohlcv_daily``).

The one place that knows how to fetch and store daily bars from Schwab's REST
``pricehistory``. Two callers:

* ``scripts/schwab_daily_refresh.py`` — the batch refresh over the stream universe;
* :func:`ensure_daily_history` — on-demand and backfill: "make sure these symbols
  have enough daily history", fetching and storing whatever is missing.

Why REST: it is NOT subject to the 300-symbol streaming cap
(``docs/ISSUES.md#schwab-stream-300-symbol-account-cap``), and returns up to
~20 years of daily bars for any symbol Schwab knows.

Invariants (the reasons this is a module and not inline code):

* **Lake first, ClickHouse second.** ``schwab_daily_raw`` is ground truth; a CH
  row with no lake row behind it is the drift this ordering prevents. If the lake
  write fails, ClickHouse is not touched.
* **Never re-fetch what we hold.** Lake appends are NOT deduplicated by Iceberg, so
  re-fetching a window that already exists would double-append there. The service
  only requests the span *before* the earliest stored day (or the whole span for a
  symbol with nothing stored); the nightly refresh owns the tail.
* **Verified, not assumed.** After the insert, the symbol's stored row count is
  read back and must have grown by what was sent.
* **No silent outcomes.** Every symbol gets a :class:`DailyEnsureResult` with a
  status and detail, and every outcome (including "nothing to do") is logged.

Schwab bars are pre-adjusted: a *future* split does not retroactively re-adjust rows
already stored (unlike the Polygon path, which re-divides history). Callers that
keep on-demand symbols long term should re-pull full history periodically.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
TABLE = "ohlcv_daily"
# ohlcv_daily stores the ET trading day stamped at 14:30 UTC — same convention as
# polygon_grouped_daily, so downstream date bucketing stays identical.
DAY_HOUR_UTC, DAY_MIN_UTC = 14, 30
COLS = ["symbol", "timestamp", "open", "high", "low", "close", "volume", "version"]

DEFAULT_MIN_SESSIONS = 250   # ~1 trading year: comfortably above what RRG needs
DEFAULT_YEARS = 3            # how far back a first fetch reaches

_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

Status = Literal[
    "ok", "already_covered", "empty", "invalid", "in_flight", "lake_failed", "ch_failed", "error"
]


@dataclass(frozen=True)
class DailyEnsureResult:
    symbol: str
    status: Status
    rows: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "already_covered")


@dataclass(frozen=True)
class LakeWriteResult:
    written: int
    failed_units: int          # failed days (per_day=True) or 1 for a failed bulk append
    messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class Coverage:
    sessions: int
    first: date | None
    last: date | None


class DailyStore(Protocol):
    """The ClickHouse side. Real implementation: :class:`ClickHouseDailyStore`."""

    def coverage(self, symbols: Sequence[str]) -> dict[str, Coverage]: ...

    def insert(self, rows: list[list]) -> None: ...


class ClickHouseDailyStore:
    def __init__(self, client: Any = None) -> None:
        if client is None:
            from app.db.client import get_client

            client = get_client()
        self._client = client

    def coverage(self, symbols: Sequence[str]) -> dict[str, Coverage]:
        if not symbols:
            return {}
        res = self._client.query(
            f"SELECT symbol, count(), min(toDate(timestamp)), max(toDate(timestamp)) "
            f"FROM {TABLE} FINAL WHERE symbol IN {{syms:Array(String)}} GROUP BY symbol",
            parameters={"syms": list(symbols)},
        )
        return {r[0]: Coverage(int(r[1]), r[2], r[3]) for r in res.result_rows}

    def insert(self, rows: list[list]) -> None:
        self._client.insert(TABLE, rows, column_names=COLS)


LakeWriter = Callable[[list[list], int], Awaitable[LakeWriteResult]]


# ── pure helpers ──────────────────────────────────────────────────────


def day_ts(d: date) -> datetime:
    return datetime.combine(d, time(DAY_HOUR_UTC, DAY_MIN_UTC), tzinfo=timezone.utc)


def valid_ticker(symbol: str) -> bool:
    return bool(_TICKER.match(symbol or ""))


def build_schwab_provider():
    """The same Schwab provider construction the batch script has always used.

    Raises ``RuntimeError`` (never exits) when there is no refresh token, so a
    server process can surface it instead of dying.
    """
    from app.config import settings
    from app.providers.schwab_provider import SchwabProvider

    token = settings.get_schwab_refresh_token()
    if not token:
        raise RuntimeError(
            "No Schwab refresh token. Run scripts/schwab_get_refresh_token.py, and note "
            "SCHWAB_REFRESH_TOKEN in .env overrides SCHWAB_REFRESH_TOKEN_FILE."
        )
    return SchwabProvider(
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        refresh_token=token,
        callback_url=settings.schwab_callback_url or None,
        base_url=settings.schwab_base_url,
        refresh_token_file=settings.schwab_refresh_token_file or None,
    )


async def fetch_daily_bars(provider: Any, sym: str, start: date, end: date) -> tuple[list[list], str]:
    """Daily bars for one symbol over ``[start, end]`` (ET dates).

    Returns ``(rows, status)``; status is ``ok``, ``empty`` (Schwab answered 200 with no
    candles at all — it does not know the symbol) or ``out_of_range`` (it has bars, none
    inside the window). Rows are ``[symbol, ts, open, high, low, close, volume]``.
    """
    df = await provider.historical_df(
        sym,
        datetime.combine(start, time(0, 0), tzinfo=timezone.utc),
        datetime.combine(end + timedelta(days=1), time(0, 0), tzinfo=timezone.utc),
        timeframe="1d",
    )
    if df is None or len(df) == 0:
        return [], "empty"
    out: list[list] = []
    for ts, r in df.iterrows():
        d = ts.astimezone(ET).date() if getattr(ts, "tzinfo", None) else ts.date()
        if not (start <= d <= end):
            continue
        out.append([sym, day_ts(d), float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]), float(r.get("volume") or 0)])
    return out, ("ok" if out else "out_of_range")


async def write_lake(batch: list[list], version: int, *, per_day: bool = False) -> LakeWriteResult:
    """Append bars to ``equities.schwab_daily_raw``.

    ``per_day=True`` does one append per trading day (the batch refresh: scopes a
    failure to one day). ``per_day=False`` is one append for the whole batch (history
    backfill: one Iceberg commit instead of hundreds). ``version`` is carried by the
    caller for CH; the lake rows get their own ingestion metadata from the sink.
    """
    import pandas as pd

    from app.services.equities.sink import EquitiesIcebergSink

    sink = EquitiesIcebergSink.for_schwab_daily_raw()
    # Schwab's daily pricehistory carries no vwap or trade count: NULL, not 0.0 (a
    # fabricated zero would read as a real value downstream).
    df = pd.DataFrame(
        [{"symbol": r[0], "timestamp": r[1], "open": r[2], "high": r[3], "low": r[4],
          "close": r[5], "volume": r[6], "vwap": None, "trade_count": None, "source": "schwab-rest"}
         for r in batch]
    )
    ok_statuses = ("ok", "written", "success")
    written = failed = 0
    msgs: list[str] = []
    chunks = (
        [(day, chunk) for day, chunk in df.groupby(df["timestamp"].dt.date)]
        if per_day
        else [(df["timestamp"].dt.date.max(), df)]
    )
    for day, chunk in chunks:
        res = await sink.write(chunk, file_date=day, kind="day", provider="schwab-rest")
        if res.status in ok_statuses:
            written += res.bars_written or len(chunk)
        else:
            failed += 1
            msgs.append(f"lake {day}: {res.status} — {res.metadata or res.error}")
    return LakeWriteResult(written=written, failed_units=failed, messages=tuple(msgs))


# ── on-demand / backfill ──────────────────────────────────────────────

_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def _claim(symbol: str) -> bool:
    with _inflight_lock:
        if symbol in _inflight:
            return False
        _inflight.add(symbol)
        return True


def _release(symbol: str) -> None:
    with _inflight_lock:
        _inflight.discard(symbol)


def is_fetching(symbol: str) -> bool:
    """True while a fetch for ``symbol`` is running in this process (used to tell a
    user "loading history" instead of "no data")."""
    with _inflight_lock:
        return (symbol or "").upper() in _inflight


async def ensure_daily_history(
    symbols: Sequence[str],
    *,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    years: int = DEFAULT_YEARS,
    provider: Any = None,
    store: DailyStore | None = None,
    lake_writer: LakeWriter | None = None,
    today: date | None = None,
    sleep_s: float = 0.05,
) -> list[DailyEnsureResult]:
    """Make sure each symbol has at least ``min_sessions`` daily bars; fetch and store
    what is missing. Never raises for a single symbol's problem: each gets a result.

    * A symbol with nothing stored gets ``[today - years, yesterday ET]``.
    * A symbol with some rows gets only the span *before* its earliest stored day.
    * A symbol already at ``min_sessions`` is left alone (``already_covered``).
    * A symbol Schwab has no data for is ``empty`` and nothing is written.

    The collaborators are injectable (tests; the API process passing its own provider).
    """
    syms = list(dict.fromkeys((s or "").strip().upper() for s in symbols if (s or "").strip()))
    results: dict[str, DailyEnsureResult] = {}
    for s in syms:
        if not valid_ticker(s):
            results[s] = DailyEnsureResult(s, "invalid", detail="not a valid ticker format")

    todo = [s for s in syms if s not in results]
    store = store or ClickHouseDailyStore()
    coverage = store.coverage(todo)
    if today is None:
        from app.services.equities.gaps import yesterday_et

        yesterday = yesterday_et()
    else:
        yesterday = today - timedelta(days=1)
    horizon = yesterday - timedelta(days=int(365.25 * years))
    writer = lake_writer or (lambda batch, version: write_lake(batch, version, per_day=False))

    need: list[tuple[str, date, date, int]] = []
    for s in todo:
        cov = coverage.get(s) or Coverage(0, None, None)
        if cov.sessions >= min_sessions:
            results[s] = DailyEnsureResult(s, "already_covered", detail=f"{cov.sessions} sessions stored")
            continue
        end = (cov.first - timedelta(days=1)) if cov.first else yesterday
        if end < horizon:
            results[s] = DailyEnsureResult(
                s, "already_covered",
                detail=f"{cov.sessions} sessions; history already reaches the {years}y horizon",
            )
            continue
        need.append((s, horizon, end, cov.sessions))

    if need and provider is None:
        provider = build_schwab_provider()
    version = int(datetime.now(timezone.utc).timestamp() * 1000)

    for s, start, end, before in need:
        if not _claim(s):
            results[s] = DailyEnsureResult(s, "in_flight", detail="another fetch for this symbol is running")
            continue
        try:
            results[s] = await _ensure_one(s, start, end, before, provider, store, writer, version)
        except Exception as exc:  # noqa: BLE001 — one symbol must not abort the rest
            logger.exception("ensure_daily_history: %s failed", s)
            results[s] = DailyEnsureResult(s, "error", detail=f"{type(exc).__name__}: {str(exc)[:160]}")
        finally:
            _release(s)
        if sleep_s:
            await asyncio.sleep(sleep_s)

    ordered = [results[s] for s in syms]
    for r in ordered:
        logger.info("ensure_daily_history: %-8s %-15s rows=%d %s", r.symbol, r.status, r.rows, r.detail)
    return ordered


async def _ensure_one(
    sym: str, start: date, end: date, before: int,
    provider: Any, store: DailyStore, writer: LakeWriter, version: int,
) -> DailyEnsureResult:
    # Re-check inside the claim: another process may have filled it since the first read.
    cov_now = store.coverage([sym]).get(sym) or Coverage(0, None, None)
    if cov_now.first is not None and cov_now.first - timedelta(days=1) < end:
        end = cov_now.first - timedelta(days=1)
        before = cov_now.sessions
        if end < start:
            return DailyEnsureResult(sym, "already_covered", detail="filled concurrently")

    rows, status = await fetch_daily_bars(provider, sym, start, end)
    if status == "empty":
        return DailyEnsureResult(sym, "empty", detail="Schwab has no price history for this symbol")
    if not rows:
        return DailyEnsureResult(sym, "empty", detail=f"no bars between {start} and {end}")

    batch = [r + [version] for r in rows]
    lake = await writer([r[:7] for r in batch], version)
    if lake.failed_units:
        return DailyEnsureResult(
            sym, "lake_failed",
            detail=f"lake write failed; ClickHouse not touched. {'; '.join(lake.messages)[:200]}",
        )

    try:
        store.insert(batch)
    except Exception as exc:  # noqa: BLE001 — reported as its own outcome
        # The lake is ahead of ClickHouse (the safe direction); a later run repairs CH
        # without re-fetching (use skip-lake semantics: coverage shows nothing stored).
        logger.exception("ensure_daily_history: %s lake written but ClickHouse insert failed", sym)
        return DailyEnsureResult(
            sym, "ch_failed",
            detail=f"lake written ({len(rows)} rows) but ClickHouse insert failed: {str(exc)[:140]}",
        )
    after = (store.coverage([sym]).get(sym) or Coverage(0, None, None)).sessions
    if after < before + len(rows):
        return DailyEnsureResult(
            sym, "error",
            detail=f"readback mismatch: {before} stored + {len(rows)} sent but {after} readable",
        )
    return DailyEnsureResult(sym, "ok", rows=len(rows), detail=f"{start} to {end}")
