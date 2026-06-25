"""Non-blocking CH gap fill — schedules lake→CH fills on a background thread.

Called by bars_gateway when CH lacks the requested window. Returns
immediately; the fill runs on a daemon thread and logs completion.

Design choices:
- threading.Thread, not asyncio.Task — bars_gateway is sync; its callers
  (routes, MCP tools) often invoke it via asyncio.to_thread(), so there is
  no running event loop in the calling thread. Daemon threads are the
  correct primitive for fire-and-forget sync work from a sync context.
- Per-symbol dedup via _IN_FLIGHT: skip spawning a thread if one is
  already running for that symbol. The fill functions themselves carry
  internal threading.Locks, so even without this guard correctness is
  maintained — the guard just avoids unnecessary thread churn.
- _lock protects _IN_FLIGHT mutations only; it is never held during the
  fill itself, so it cannot be a bottleneck.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# symbol_upper → running Thread
_IN_FLIGHT: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def schedule_gap_fill(
    symbol: str,
    start: datetime,
    end: datetime,
) -> None:
    """Fire a lake→CH fill for `symbol` on a background daemon thread.

    Safe to call from any context — sync, async handler, or a thread
    spawned by asyncio.to_thread(). Never blocks; never raises.

    The fill function is selected by asset class (futures vs equities)
    inside the thread, keeping the caller decoupled from that detail.
    """
    sym = symbol.upper()

    with _lock:
        existing = _IN_FLIGHT.get(sym)
        if existing is not None and existing.is_alive():
            logger.debug("gap_fill_worker: %s already in-flight, skip", sym)
            return

        t = threading.Thread(
            target=_run_fill,
            args=(sym, start, end),
            name=f"gap_fill:{sym}",
            daemon=True,
        )
        _IN_FLIGHT[sym] = t
        t.start()
        logger.debug("gap_fill_worker: scheduled fill for %s", sym)


def _run_fill(symbol: str, start: datetime, end: datetime) -> None:
    """Thread body — calls the appropriate sync fill and logs the result.

    Two-tier, ground-truth-first:
      1. Lake → CH (authoritative, fast). Covers any symbol the weekly
         Spark / nightly jobs have ingested.
      2. If the lake fill comes up EMPTY (inserted 0), the lake has
         nothing for this window — a genuine gap, typically a brand-new
         /cold symbol. Fall to a provider REST fill so the chart
         self-heals on demand (gated by `symbol_gapfill_enabled`).
    """
    try:
        from app.services.readers.bars_gateway import _lake_fill_fn

        fill_fn = _lake_fill_fn(symbol)
        inserted = fill_fn(symbol, start, end)
        if inserted:
            logger.info("gap_fill_worker: %s +%d rows (%s → %s)", symbol, inserted, start.date(), end.date())
        else:
            logger.debug("gap_fill_worker: %s — no rows to fill (%s → %s)", symbol, start.date(), end.date())
            # Lake (ground truth) had nothing → detected gap. Provider fallback.
            _maybe_provider_gap_fill(symbol, start, end)
    except Exception as exc:
        logger.warning("gap_fill_worker: %s fill failed: %s", symbol, exc)
    finally:
        with _lock:
            # Only remove our own entry; a subsequent schedule may have
            # already replaced it while the fill was running.
            t = _IN_FLIGHT.get(symbol)
            if t is not None and not t.is_alive():
                _IN_FLIGHT.pop(symbol, None)


def _maybe_provider_gap_fill(symbol: str, start: datetime, end: datetime) -> None:
    """Provider REST fallback when the lake can't cover a requested window.

    The edge case: a brand-new / cold symbol the lake hasn't ingested yet.
    Runs the Schwab tip-fill (provider REST → schwab_universe lake + CH,
    idempotent) so the chart self-heals for the recent (entitlement-
    bounded) window; deeper history arrives via the nightly/weekly jobs.

    Gated by `symbol_gapfill_enabled`. Equities only in v1 — futures and
    a pluggable per-fill provider knob are follow-ups (see
    docs/symbol_onboarding_read_design.md §3.3). Runs on this daemon
    thread (no event loop), so the async tip-fill is driven via
    asyncio.run. Never raises.
    """
    from app.config import settings

    if not getattr(settings, "symbol_gapfill_enabled", True):
        return

    from app.services.futures.symbols import is_futures_symbol

    if is_futures_symbol(symbol):
        return  # futures provider gap-fill: follow-up

    try:
        import asyncio

        from app.services.ingest.schwab_tip_fill import SchwabTipFill

        tip = SchwabTipFill.from_settings()
        res = asyncio.run(tip.tip_fill(symbol))
        logger.info(
            "gap_fill_worker: provider gap-fill %s fetched=%d lake=%d ch=%d",
            symbol, res.bars_fetched, res.bars_written_bronze, res.bars_written_ch,
        )
    except Exception as exc:  # noqa: BLE001 — boundary; never break the worker
        logger.warning("gap_fill_worker: provider gap-fill failed for %s: %s", symbol, exc)


def in_flight_symbols() -> list[str]:
    """Return symbols currently being filled. For health/debug endpoints."""
    with _lock:
        return [s for s, t in _IN_FLIGHT.items() if t.is_alive()]
