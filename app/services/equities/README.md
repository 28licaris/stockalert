# Equities lake

Owns the architecture-v2 equities Iceberg schemas, table creation, writes,
coverage/gap queries, and lake-to-ClickHouse fill behavior. The canonical lake
design is [`../../../docs/architecture_v2/`](../../../docs/architecture_v2/).

| Area | Files |
|---|---|
| Contracts | `models.py`, `schemas.py` |
| Storage | `tables.py`, `sink.py` |
| Coverage | `gaps.py`, `athena_coverage.py`, `athena_extract.py` |
| Hot-tier fill | `lake_to_ch_fill.py` |

Provider downloads and schedules belong to [`../ingest/`](../ingest/). Consumer
response shapes belong to [`../readers/`](../readers/). Tests live in
[`tests/`](tests/) and use injected or mocked storage clients.
