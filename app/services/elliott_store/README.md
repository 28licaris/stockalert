# Elliott Wave label store

Persists computed Elliott Wave labels in Iceberg and orchestrates recomputation.
It bridges the pure wave engine with durable lake tables; it does not define
wave rules or HTTP/MCP presentation.

| File | Purpose |
|---|---|
| `schema.py`, `tables.py` | Iceberg schema and idempotent table creation |
| `sink.py` | Label writer |
| `recompute.py` | Symbol, universe, and scheduled recomputation |

The pure engine lives in [`../../signals/elliott/`](../../signals/elliott/).
Reader-facing shapes live in [`../readers/`](../readers/). Unit tests live in
[`tests/`](tests/).
