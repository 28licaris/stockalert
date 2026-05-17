# Live service

Real-time streaming + watchlist state management. Hot-tier subscribers.

## What lives here

| Module | Purpose |
|---|---|
| `watchlist_service.py` | Tracks active streaming symbols. Subscribes to `STREAM_PROVIDER` for the current watchlist. Soft-fails when provider creds are missing (logs and continues). |
| `monitor_service.py` | Per-symbol divergence monitor (RSI / MACD / TSI), fed by live bars. |
| `monitor_manager.py` | Lifecycle manager for all active monitors. |

## Contracts other modules import from

- `from app.services.live.watchlist_service import watchlist_service`
- `from app.services.live.monitor_manager import monitor_manager`

## Independence

Subscribes to live data from whichever provider satisfies
`STREAM_PROVIDER` (default: falls back to `DATA_PROVIDER`). If the
provider credentials are missing or the connection drops, the service
logs and continues — neither symbol-page rendering nor backfill
depends on it. Other services (journal sync, nightly bronze ingest,
HTTP routes) continue to function.
