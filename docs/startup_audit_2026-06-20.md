# Startup Sequence Audit — 2026-06-20

## Summary

The nightly loops (Polygon, Schwab, Futures, CH Reconcile) are all
correctly gated — they sleep via `_seconds_until_next_run()` and do
not hit any external API until their scheduled UTC hour.

**One confirmed issue**: an initial gap sweep fires 30 seconds after
startup and immediately calls the configured history provider (Polygon
or Schwab) for every streaming symbol.

## What happens at startup (in order)

| Step | What | Outcome |
|------|------|---------|
| 1 | `init_schema()` — ClickHouse DDL | No external API |
| 2 | `batcher.start()` — equities + futures | No external API |
| 3 | `backfill_service.start()` — kicks `_gap_sweeper_loop` | Loop sleeps until 06:00 UTC; **no API call** |
| 4 | `stream_service.start()` — opens WebSocket to streaming provider | Live stream connection (expected) |
| 5 | `watchlist_service.start()` | DB read only |
| 6 | `live_lake_writer.start()` (if enabled) | Starts periodic timer; no immediate API |
| 7 | `journal_sync_service.start()` (if enabled + creds) | Starts periodic timer; no immediate API |
| 8 | Nightly loops created: `nightly_polygon_refresh`, `nightly_schwab_refresh`, `nightly_futures_refresh`, `ch_reconcile` | Each sleeps via `_seconds_until_next_run()`; **no immediate API call** |
| 9 | `_initial_gap_sweep_after_warmup` task created | **Fires 30s after startup** |

## The problem — step 9

`main_api.py` line 322:

```python
async def _initial_gap_sweep_after_warmup() -> None:
    await asyncio.sleep(30.0)               # ← 30s after startup…
    result = backfill_service.sweep_now()   # ← calls history provider for all streaming symbols
```

`sweep_now()` calls `enqueue_gap_fill()` for every currently-streaming
symbol. Each gap_fill job calls `_loader_or_build()` → `get_history_provider()`
→ constructs the Polygon (or Schwab) client and fetches historical bars.

Result: ~30 seconds after every `uvicorn` restart, the app hits the
Polygon (or Schwab) history API for every symbol on the watchlist.

## Fix recommendation

**Option A — Remove the startup sweep entirely** (simplest).
The daily gap sweeper at 06:00 UTC is the correct time to heal gaps.
The nightly reconcile (ch_reconcile at 23:00 UTC) already fills the
lake → CH. A startup sweep adds nothing but noise.

```python
# main_api.py — remove lines 312-323 entirely
```

**Option B — Gate behind a setting** (more flexible).
Add `BACKFILL_STARTUP_SWEEP_ENABLED=false` to settings and env.
Default to `false` for production; enable only in dev if desired.

**Option C — Keep but throttle** (status quo + guard).
The gap_fill has a 6-hour per-symbol cooldown already, so rapid
restarts won't hammer the provider. Only first restart after 6h hits
the API. This matches the original intent (heal gaps that opened while
down) but is still surprising to operators.

**Recommended: Option A.** The startup sweep pre-dates the nightly
reconcile. Now that ch_reconcile runs every night at 23:00 UTC, the
startup sweep is redundant. Remove it; nightly maintenance is the
correct model.

## No other startup API calls found

- All nightly loops sleep before first run ✅
- Backfill `_gap_sweeper_loop` sleeps until 06:00 UTC ✅
- Stream service opens a WebSocket (live streaming, expected) ✅
- Live lake writer timer starts but doesn't immediately flush ✅
