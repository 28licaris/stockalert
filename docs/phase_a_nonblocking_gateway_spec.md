# Phase A — Non-Blocking Bars Gateway

**Status:** Spec (pending approval)
**Scope:** `app/services/readers/bars_gateway.py` + new `app/services/live/gap_fill_worker.py`

---

## Problem

`bars_gateway.get_chart_bars()` and `get_range_bars()` — both called in
the HTTP hot path — synchronously call `fill_ch_from_futures_lake_sync()`
(or the equities equivalent) when `_ch_lacks_window()` returns True.

This blocks the HTTP response while the lake fill runs:

- Futures `/ES 1h` window = 90 days, CH only has 43 days → fill runs,
  finds nothing in the lake either, blocks for ~2-5 seconds.
- AAPL 1d: before the `_cached_compute` fix, this blocked the wave
  compute. The bars gateway itself still blocks on equities lake fills.
- Under concurrent users, fills for the same symbol can stack (no dedup).

## Proposed Architecture

```
HTTP request → bars_gateway.get_chart_bars()
               │
               ├─ Query CH immediately (always fast, <50ms)
               │
               ├─ if AUTO and _ch_lacks_window():
               │    fire asyncio.create_task(gap_fill_worker.enqueue(symbol, window))
               │    (non-blocking; task runs in background)
               │
               └─ Return CH bars immediately (partial is fine; next request gets fresh)

gap_fill_worker (background):
  ├─ Per-(symbol, table) in-flight dedup: skip if already filling
  ├─ asyncio.to_thread(fill_fn)   ← sync lake fill off the event loop
  ├─ Log completion with row count
  └─ Release in-flight lock
```

## Files Changed

### `app/services/readers/bars_gateway.py`

Remove the two blocking fill blocks (lines 150-164 and 205-216).
Replace each with a non-blocking fire-and-forget:

```python
# BEFORE (blocking):
if _ch_lacks_window(bars, start):
    inserted = _lake_fill_fn(symbol)(symbol.upper(), start, end)
    if inserted > 0:
        bars = ch_reader.get_bars_for_chart(...)

# AFTER (non-blocking):
if _ch_lacks_window(bars, start):
    from app.services.live.gap_fill_worker import schedule_gap_fill
    schedule_gap_fill(symbol, start, end)
    # Return CH bars as-is (may be partial); next poll gets the filled result
```

Same change in `get_range_bars()`.

The `BarSource.LAKE` path and the futures-daily short-circuit are unchanged.

### `app/services/live/gap_fill_worker.py` (new)

```python
"""Non-blocking CH gap fill — fires lake→CH fills as background tasks.

Called by bars_gateway when CH lacks the requested window. Returns
immediately; the fill runs off the event loop and logs completion.
Dedup: at most one fill per (symbol, table) at a time.
"""
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# (symbol_upper, table_name) → running Task
_IN_FLIGHT: dict[tuple[str, str], asyncio.Task] = {}


def schedule_gap_fill(symbol: str, start: datetime, end: datetime) -> None:
    """Fire a background lake→CH fill if one isn't already running."""
    from app.services.readers.bars_gateway import _lake_fill_fn, ch_table_for

    sym = symbol.upper()
    table = ch_table_for(sym)
    key = (sym, table)

    existing = _IN_FLIGHT.get(key)
    if existing and not existing.done():
        logger.debug("gap_fill_worker: %s already in flight, skip", sym)
        return

    async def _run():
        try:
            fill_fn = _lake_fill_fn(sym)
            inserted = await asyncio.to_thread(fill_fn, sym, start, end)
            if inserted:
                logger.info("gap_fill_worker: %s filled %d rows", sym, inserted)
            else:
                logger.debug("gap_fill_worker: %s — nothing to fill", sym)
        except Exception as exc:
            logger.warning("gap_fill_worker: %s failed: %s", sym, exc)
        finally:
            _IN_FLIGHT.pop(key, None)

    task = asyncio.create_task(_run(), name=f"gap_fill:{sym}")
    _IN_FLIGHT[key] = task
```

## What Changes for Users

- First load of a symbol with a coverage gap: returns CH data immediately
  (may be partial). A spinner in the UI is all they see.
- Subsequent load (seconds later): fill has completed, full data is returned.
- No change to `BarSource.CLICKHOUSE` or `BarSource.LAKE` paths.
- No change to the nightly reconcile, Elliott Wave compute, or MCP tools.

## Not In Scope

- Backpressure / task queue limits (Phase C if needed — one in-flight
  fill per symbol is enough dedup for now).
- Websocket push when fill completes (nice to have; not blocking).
- Removing the startup gap sweep (tracked in startup audit doc; separate change).

## Test Plan

1. `GET /api/v1/bars?symbol=/ES&interval=1h` — no longer blocks; returns
   within 100ms even on first load after CH is cold.
2. Two concurrent requests for the same cold symbol — only one fill task
   created (check logs).
3. `BarSource.LAKE` and `BarSource.CLICKHOUSE` explicit paths — unchanged.
4. Add unit test for `schedule_gap_fill()` dedup logic (mock `_lake_fill_fn`).
