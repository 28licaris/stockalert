# HTTP API

Thin FastAPI adapters over services and readers. Routes validate input, obtain
dependencies, call one domain operation, and return declared Pydantic models.

## Boundaries

- `routes_*.py` owns transport concerns only; do not add business logic here.
- [`schemas/`](schemas/) owns HTTP request and response models.
- Domain behavior belongs in [`../services/`](../services/).
- Authentication dependencies shared by routes live in
  [`auth_dependencies.py`](auth_dependencies.py).

When adding a route, use a response model, preserve the `/api` namespace in the
surrounding router, and expose the same read behavior through MCP when agents
need it. Start at [`../main_api.py`](../main_api.py) to see router registration.

## Tests

Route and schema unit tests live in [`tests/`](tests/). Live HTTP tests that
mutate ClickHouse live in [`../../tests/integration/`](../../tests/integration/).

```bash
poetry run pytest app/api/tests -m "not integration"
```
