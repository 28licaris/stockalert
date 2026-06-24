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
    """Thread body — calls the appropriate sync fill and logs the result."""
    try:
        from app.services.readers.bars_gateway import _lake_fill_fn

        fill_fn = _lake_fill_fn(symbol)
        inserted = fill_fn(symbol, start, end)
        if inserted:
            logger.info("gap_fill_worker: %s +%d rows (%s → %s)", symbol, inserted, start.date(), end.date())
        else:
            logger.debug("gap_fill_worker: %s — no rows to fill (%s → %s)", symbol, start.date(), end.date())
    except Exception as exc:
        logger.warning("gap_fill_worker: %s fill failed: %s", symbol, exc)
    finally:
        with _lock:
            # Only remove our own entry; a subsequent schedule may have
            # already replaced it while the fill was running.
            t = _IN_FLIGHT.get(symbol)
            if t is not None and not t.is_alive():
                _IN_FLIGHT.pop(symbol, None)


def in_flight_symbols() -> list[str]:
    """Return symbols currently being filled. For health/debug endpoints."""
    with _lock:
        return [s for s, t in _IN_FLIGHT.items() if t.is_alive()]
