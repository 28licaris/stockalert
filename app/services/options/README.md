# Options service

Owns the options market-data contracts and parsing logic. The first supported
source is Schwab option-chain REST payloads normalized into canonical contract
snapshots and derived gamma exposure rows.

Public cross-service imports come from `schemas.py` and `contract.py` only.
`parser.py` is implementation detail for ingestion and tests.

Current scope:

- Pydantic DTOs for option-chain raw snapshots, contract snapshots, expirations,
  parse results, and gamma exposure rows.
- Offline Schwab chain parser from fixture/provider payloads.
- Gamma exposure calculation from contract gamma, open interest, multiplier,
  underlying price, and put/call side.
- Idempotent Iceberg table creation for the `options` Glue namespace.
- Iceberg sink for parsed Schwab chain snapshots and derived GEX rows.
- Snapshot orchestration for one underlying: Schwab REST chain fetch, parse,
  derived GEX calculation, and sink write.
- Operator CLI for explicit, active-universe, and watchlist snapshots:
  `scripts/options_chain_snapshot.py`.
- Scheduled snapshot refresh wrapper:
  `app/services/ingest/options_snapshot_refresh.py`.
- Lake reader for canonical contracts and GEX:
  `app/services/readers/options_reader.py`.
- HTTP and MCP read surfaces:
  `app/api/routes_options.py` and `app/mcp/tools/options.py`.
- ClickHouse hot-tier latest cache:
  `app/services/options/hot_sink.py` and
  `app/services/readers/options_hot_reader.py`.

Not yet owned here:

- Schwab option streaming.

Test with:

```bash
poetry run pytest app/services/options/tests
poetry run python scripts/options_chain_snapshot.py --symbols AAPL,MSFT --dry-run
poetry run python scripts/options_chain_snapshot.py --symbols active --dry-run
poetry run python scripts/options_chain_snapshot.py --symbols watchlist:momentum --dry-run
```
