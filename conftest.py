"""
Shared pytest fixtures.

ClickHouse must be running locally (docker-compose up clickhouse) for the
integration suites. Tests that need ClickHouse should depend on the
`clickhouse_ready` fixture which skips gracefully if the database isn't up.
"""
from __future__ import annotations

import os
import sys

import pytest

# Make `app.*` and `tests.*` importable when running `pytest` from anywhere.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


@pytest.fixture(scope="session")
def clickhouse_ready() -> bool:
    """Skip dependent tests if ClickHouse isn't reachable; else ensure schema exists."""
    from app.db import init_schema, ping

    if not ping():
        pytest.skip("ClickHouse not reachable (start it via docker-compose up clickhouse)")
    init_schema()
    return True
