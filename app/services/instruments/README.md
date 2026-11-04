# instruments/

Durable instrument reference data — company name, exchange, asset type.

## What this owns

A **durable ClickHouse `instrument_names` table as the source of truth** for
symbol descriptions, with lazy backfill from Polygon's reference endpoint.

The live provider (Schwab) does not reliably resolve instrument descriptions in
every deployment, which left customer-facing surfaces — watchlists, stream rows
— showing bare tickers. Caching the answer durably fixes that, and caching
**negatives too** means a given symbol hits Polygon at most once.

Note the entitlement distinction: Polygon's `/v3/reference/tickers` is a
*different* entitlement from the flat-files, so this keeps working even though
the flat-file subscription lapsed.

Does not own: the streaming universe (see `universe/`), watchlist membership
(see `watchlists/`), or sector/theme classification (see `sectors/`).

## Layout

| File | Owns |
|---|---|
| [names.py](names.py) | `resolve_names`, `warm`, `warm_stream_universe`, `refresh_names`, `run_names_refresh_loop` |

## Public contract

```python
resolve_names(symbols) -> {SYMBOL: {"description", "exchange", "asset_type"}}
```

**Always returns an entry for every requested symbol** — empty strings when
still unresolved, never a missing key. Callers never need a `.get()` guard.

Synchronous and ClickHouse-backed, so call it from an async route via
`asyncio.to_thread`.

| Function | Use |
|---|---|
| `resolve_names(symbols)` | The read path — cache hit, else fill from Polygon |
| `warm(symbols)` | Pre-resolve a known set (e.g. a watchlist on open) |
| `warm_stream_universe()` | Startup warm of the whole stream universe |
| `refresh_names()` / `run_names_refresh_loop()` | Re-resolve blanks; the loop is the scheduled job |

Consumed by `routes_instruments.py`, `routes_my_watchlists.py`, and the startup
sequence in `main_api.py`.

## Testing

```bash
poetry run pytest app/services/instruments/tests -q
```

Unit tests mock the Polygon client and assert the cache contract — including
that a negative result is cached, so a second call makes no second request.
ClickHouse-backed tests carry the `integration` marker.
