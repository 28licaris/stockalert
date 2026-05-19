# Service Modules — Shape and Composition

Every microservice / module folder in this repo follows the same shape.
This is what keeps the modular monolith liftable to per-service
containers later, and what keeps modules testable in isolation today.

## Folder template

Every microservice / module folder in `app/services/` (and the future
`trading_ai/<service>/`) follows this shape:

```
service_name/
├── schemas.py       Pydantic DTOs — the only file other services import
├── contract.py      Protocol class — the public interface
├── service.py       Implementation — NEVER imported across service boundaries
├── README.md        What it owns, contract, how to test
└── tests/
```

Reference: `app/services/bronze/` is the canonical example
(`schemas.py`, `tables.py`, `sink.py`, `gaps.py`, `README.md`).

## Rules

### 1. Cross-service imports come from `schemas.py` or `contract.py` only

Never `service.py`. This is what makes a service liftable to its own
container — callers depend on the contract, not the implementation.

### 2. Factories beat inheritance

New variant = new classmethod, not a subclass.

```python
sink = BronzeIcebergSink.for_polygon_minute()
sink = BronzeIcebergSink.for_schwab_minute()
```

One class, multiple table targets.

### 3. `from_settings()` is the single place global config touches a service

The constructor takes dependencies explicitly. `from_settings()` is the
factory for the common production-construction path. Keeps the hot path
injection-friendly and the unit tests trivial.

```python
class SilverBuilder:
    def __init__(self, *, catalog: Catalog, ch_client: Client, ...):
        ...

    @classmethod
    def from_settings(cls) -> "SilverBuilder":
        from app.config import settings
        return cls(
            catalog=load_catalog(settings.glue_database),
            ch_client=make_client(settings.ch_url),
        )
```

### 4. Return result objects, don't raise

Sinks and clients return `SinkResult(status, error, metadata)` or
similar for expected outcomes (`"ok"`, `"skipped"`, `"error"`). Reserve
exceptions for catastrophic, abort-the-whole-batch problems. This way
one sink's failure does not take down others in a fan-out.

See also [`coding.md`](coding.md) rule 8.

### 5. Lazy imports for cross-package deps

Put `from app.X import Y` inside the function body when the dependency
would otherwise pull heavy modules at import time. Especially for
`boto3`, `clickhouse-connect`, `pyiceberg` — avoids slowing every
import and breaks circular cycles.

```python
def build_sink() -> "BronzeIcebergSink":
    from app.services.bronze.sink import BronzeIcebergSink  # lazy
    return BronzeIcebergSink.for_polygon_minute()
```

### 6. Idempotent everything

Re-runs are no-ops at the highest level possible. ClickHouse uses
`ReplacingMergeTree(version)`. Iceberg uses watermarks + append (see
[`data/bronze_idempotency.md`](data/bronze_idempotency.md)). Nightly
jobs and CLIs are designed so `--start X --end X` twice gives the same
final state as once.

## Anti-patterns

- A module that imports `another_service.service.X` (instead of
  `another_service.schemas` or `.contract`). Breaks the boundary.
- Long constructors with many positional args (use kwargs + factories).
- Sinks that raise on every error path. Loses the "fan-out where one
  failure doesn't poison others" property.
- `os.environ.get(...)` scattered across modules. All config goes
  through `app.config.settings` and the `from_settings()` factory.

## Related

- [`platform_design.md`](platform_design.md) — why these rules exist
  (contract-first, lift-out test).
- [`testing.md`](testing.md) — the contract is the test surface.
- [`doc_discipline.md`](doc_discipline.md) — README per service.
