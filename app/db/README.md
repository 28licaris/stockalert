# Database adapters

Infrastructure access for ClickHouse and identity PostgreSQL. This package owns
clients, schema initialization, low-level queries, batching, and repositories;
it does not own domain policy.

| File | Purpose |
|---|---|
| `client.py`, `init.py` | ClickHouse lifecycle and schema |
| `queries.py`, `batcher.py` | Market-data queries and batched writes |
| `watchlist_repo.py`, `journal_repo.py` | ClickHouse repositories |
| `postgres.py` | Identity PostgreSQL connection boundary |

Services call these adapters and translate records into their public schemas.
Tests that exercise a real database are integration tests under
[`../../tests/integration/`](../../tests/integration/); depend on the shared
`clickhouse_ready` fixture rather than opening an unmanaged test client.
