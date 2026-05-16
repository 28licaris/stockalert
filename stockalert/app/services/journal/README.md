# Journal service

**Schwab-only**: pulls account balances + trade activity from the
Schwab Trader API every 5 minutes into ClickHouse for trade-performance
analysis. This service is intentionally provider-specific — Schwab is
the user's actual broker, and journal data only makes sense from the
account where trades actually execute.

## What lives here

| Module | Purpose |
|---|---|
| `journal_sync.py` | Periodic loop. Calls Schwab `/accounts` (balances) and the activity API (fills). Writes to CH `account_snapshots` + `trades`. |
| `journal_parser.py` | Schwab activity-record parser (multiple shapes — orders, executions, dividends, splits). |
| `pnl.py` | Position + realized/unrealized PnL math used by both the sync loop and `routes_journal`. |

## Contracts other modules import from

- `from app.services.journal.journal_sync import journal_sync_service`
- `from app.services.journal.journal_parser import TradeRecord, parse_transaction`
- `from app.services.journal.pnl import …` (utility functions)

## Independence

Gated by `JOURNAL_ENABLED=true` + valid Schwab credentials. If those
aren't satisfied, the loop simply isn't started — main_api.py logs an
info line and continues. Failure of the journal sync does NOT affect:

- Live streaming (`live/watchlist_service`)
- Nightly bronze ingest (`ingest/nightly_*`)
- Dashboard chart routes (CH read-only)
- Lake reads (Iceberg)

This is the "service isolation" property requested for the local-server
deployment.
