# Market-data providers

External-provider adapters for Alpaca, Polygon, and Schwab. [`base.py`](base.py)
defines the common streaming and historical-data interface; concrete modules
translate provider payloads into platform shapes.

Provider modules may know vendor APIs but must not own lake, ClickHouse, API, or
strategy policy. Select providers through [`../config.py`](../config.py), not
with direct environment reads. Corp-action and flat-file clients are Polygon
specialized inputs consumed by ingestion services.

Unit tests use mocked transport in [`tests/`](tests/). Credentialed checks are
manual or integration tests:

```bash
poetry run pytest app/providers/tests
```
