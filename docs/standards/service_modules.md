# Service Modules — Shape

Every module folder in `app/services/` (and future
`trading_ai/<service>/`) follows this shape:

```
service_name/
├── schemas.py    Pydantic DTOs — only file other services import
├── contract.py   Protocol — public interface
├── service.py    Implementation — NEVER imported across services
├── README.md     What it owns, contract, how to test
└── tests/        Unit tests owned by this service
```

Canonical example: `app/services/stream/`.

## Rules

1. **Cross-service imports come from `schemas.py` / `contract.py`
   only.** Never `service.py`. This is what keeps services liftable
   to containers.

2. **Factories beat inheritance.** New variant = new classmethod.

   ```python
   sink = BronzeIcebergSink.for_polygon_minute()
   sink = BronzeIcebergSink.for_schwab_minute()
   ```

3. **`from_settings()` is the single place global config touches a
   service.** Constructor takes deps explicitly.

   ```python
   class Builder:
       def __init__(self, *, catalog, ch_client): ...

       @classmethod
       def from_settings(cls):
           from app.config import settings
           return cls(catalog=load_catalog(settings.db), ...)
   ```

4. **Return result objects, don't raise** for predictable failures.
   `SinkResult(status, error, metadata)` for `"ok" | "skipped" |
   "error"`. Exceptions only for abort-the-batch problems. See
   [`coding.md`](coding.md) §8.

5. **Lazy imports for heavy cross-package deps** (boto3, pyiceberg,
   clickhouse-connect). Inside the function body, not module top.

   ```python
   def build():
       from app.services.bronze.sink import BronzeIcebergSink
       return BronzeIcebergSink.for_polygon_minute()
   ```

6. **Idempotent everything.** `--start X --end X` twice = same result
   as once. CH: `ReplacingMergeTree(version)`. Iceberg: watermarks +
   append.

7. **Tests follow ownership.** Pure service tests live in the service's
   `tests/` folder. Cross-service contract tests live in repository-level
   `tests/contract/`; live-infrastructure tests live in `tests/integration/`.

## Forbidden

- Importing `another_service.service.X` (use `schemas` / `contract`).
- Long positional-arg constructors.
- Sinks that raise on every error path.
- `os.environ.get(...)` outside `app/config.py`.
