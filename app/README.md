# Application packages

`app/` contains the production Python application. Treat each child package as
an ownership boundary; read its README before editing it.

| Package | Owns |
|---|---|
| [`api/`](api/) | FastAPI adapters and HTTP schemas |
| [`db/`](db/) | ClickHouse and PostgreSQL access |
| [`indicators/`](indicators/) | Pure technical-analysis math |
| [`mcp/`](mcp/) | Agent-facing MCP adapters |
| [`providers/`](providers/) | External market-data clients |
| [`services/`](services/) | Domain behavior and orchestration |
| [`signals/`](signals/) | Pure market-pattern detection |

`config.py` is the only environment-variable boundary. `main_api.py` composes
the packages and owns process startup; domain logic does not belong in either
file. `logging_setup.py` owns the process log format and **secret redaction**:
the HTTP clients are held at WARNING and every formatted line masks credential
parameters (`apiKey`, `UserID`, `registrationkey`, tokens). A provider that
puts a credential in a URL adds its parameter name there.

Unit tests live beside their owning package in `tests/`. Cross-package contract
tests live in [`../tests/contract/`](../tests/contract/), and tests requiring
live infrastructure live in [`../tests/integration/`](../tests/integration/).
