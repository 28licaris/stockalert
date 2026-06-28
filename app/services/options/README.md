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

Not yet owned here:

- Schwab provider orchestration.
- HTTP routes.
- MCP tools.
- ClickHouse hot tables or streaming.

Test with:

```bash
poetry run pytest app/services/options/tests
```
