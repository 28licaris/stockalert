# Repository-level tests

Most unit tests are colocated with their owning module under `<module>/tests/`.
This directory is reserved for tests and support that intentionally span module
boundaries.

| Directory | Purpose |
|---|---|
| [`contract/`](contract/) | In-process cross-module/API/MCP contracts; no live services |
| [`integration/`](integration/) | Live ClickHouse, PostgreSQL, S3, provider, or model checks; marked `integration` |
| [`manual/`](manual/) | Operator-run diagnostics that are not pytest assertions |
| [`support/`](support/) | Fixtures shared by more than one module |

Shared pytest setup lives in [`../conftest.py`](../conftest.py), making it
available to both colocated and repository-level tests.
