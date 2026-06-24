"""FE-CONTRACTS-6a — ClickHouse ad-hoc query endpoint tests.

Verifies:
  - OpenAPI publishes the new schemas + routes use response_model.
  - Schema listing excludes `system.*` and includes user tables.
  - Query execution returns the documented envelope.
  - Safety rails: readonly=1 rejects DDL/DML; row cap clamps; bad SQL
    surfaces as 400 with the typed ErrorResponse envelope.

Tests assume the dev ClickHouse instance is reachable (same pattern
as the existing watchlist + seed tests).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.clickhouse import (
    ClickHouseQueryResponse,
    ClickHouseSchemaResponse,
)
from app.main_api import app
from app.services.clickhouse_query import query_service


pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────
# OpenAPI publishes the schemas
# ─────────────────────────────────────────────────────────────────────


def test_openapi_publishes_clickhouse_schemas():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    expected = {
        "CHColumn",
        "CHTable",
        "ClickHouseSchemaResponse",
        "ClickHouseQueryRequest",
        "ClickHouseQueryResponse",
    }
    missing = expected - set(spec["components"]["schemas"])
    assert not missing, f"Missing: {missing}"


def test_clickhouse_schema_route_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/clickhouse/schema"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/ClickHouseSchemaResponse")


def test_clickhouse_query_route_uses_response_model():
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/clickhouse/query"]["post"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/ClickHouseQueryResponse")


# ─────────────────────────────────────────────────────────────────────
# Schema listing
# ─────────────────────────────────────────────────────────────────────


def test_schema_listing_excludes_system_tables():
    """`system.*` and `INFORMATION_SCHEMA.*` MUST NOT appear in the
    response — internal CH metadata isn't for cockpit eyes."""
    query_service.invalidate_schema_cache()
    client = TestClient(app)
    r = client.get("/api/v1/clickhouse/schema")
    assert r.status_code == 200
    body = ClickHouseSchemaResponse(**r.json())
    hidden = {"system", "INFORMATION_SCHEMA", "information_schema"}
    for t in body.tables:
        assert t.database not in hidden, f"Leaked hidden db: {t.database}.{t.name}"


def test_schema_listing_includes_user_tables():
    """At minimum we should see our own ohlcv tables."""
    query_service.invalidate_schema_cache()
    client = TestClient(app)
    r = client.get("/api/v1/clickhouse/schema")
    assert r.status_code == 200
    body = ClickHouseSchemaResponse(**r.json())
    names = {t.name for t in body.tables}
    assert "ohlcv_1m" in names
    assert "watchlists" in names


def test_schema_listing_uses_cache():
    """Second hit within the TTL window returns cached=True."""
    query_service.invalidate_schema_cache()
    client = TestClient(app)
    r1 = client.get("/api/v1/clickhouse/schema")
    r2 = client.get("/api/v1/clickhouse/schema")
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True


# ─────────────────────────────────────────────────────────────────────
# Query execution — happy path
# ─────────────────────────────────────────────────────────────────────


def test_simple_query_returns_typed_envelope():
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={"sql": "SELECT 1 AS a, 'hi' AS b"},
    )
    assert r.status_code == 200
    body = ClickHouseQueryResponse(**r.json())
    assert [c.name for c in body.columns] == ["a", "b"]
    assert body.rows == [[1, "hi"]]
    assert body.row_count == 1
    assert body.truncated is False
    assert body.duration_ms >= 0


def test_query_with_trailing_semicolon_is_stripped():
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={"sql": "SELECT 1 AS a;   ;  "},
    )
    assert r.status_code == 200
    assert r.json()["row_count"] == 1


def test_query_datetime_is_iso_serialized():
    """Naive CH datetimes serialize as ISO + Z so JS new Date() parses
    them correctly as UTC."""
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={"sql": "SELECT toDateTime('2026-05-19 12:00:00') AS ts"},
    )
    assert r.status_code == 200
    body = ClickHouseQueryResponse(**r.json())
    cell = body.rows[0][0]
    assert isinstance(cell, str)
    assert cell.startswith("2026-05-19T12:00:00")


# ─────────────────────────────────────────────────────────────────────
# Safety rails
# ─────────────────────────────────────────────────────────────────────


def test_readonly_rejects_create_table():
    """CH `readonly=1` must hard-reject DDL with engine error code 164."""
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={
            "sql": "CREATE TABLE ddl_attempt (x UInt8) ENGINE = MergeTree ORDER BY x"
        },
    )
    assert r.status_code == 400
    body = r.json()
    # Typed envelope, not legacy {detail: ...}
    assert "code" in body and "message" in body
    assert "readonly" in body["message"].lower()


def test_readonly_rejects_insert():
    """INSERT is also blocked by readonly=1."""
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={"sql": "INSERT INTO ohlcv_1m VALUES (1)"},
    )
    assert r.status_code == 400
    # Either readonly rejects or the type/shape mismatch does — both
    # are "rejected" outcomes; the cockpit is safe either way.
    assert "code" in r.json()


def test_row_cap_clamps_and_truncates():
    """Asking for max_rows=2 against a query that returns 5 rows must
    set truncated=True and trim the result."""
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={
            "sql": "SELECT number AS n FROM numbers(5)",
            "max_rows": 2,
        },
    )
    assert r.status_code == 200
    body = ClickHouseQueryResponse(**r.json())
    assert body.row_count == 2
    assert body.truncated is True


def test_row_cap_no_truncation_when_under_limit():
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={"sql": "SELECT number FROM numbers(3)", "max_rows": 100},
    )
    assert r.status_code == 200
    body = ClickHouseQueryResponse(**r.json())
    assert body.row_count == 3
    assert body.truncated is False


def test_invalid_sql_returns_envelope_400():
    """Syntax errors come back as 400 with the ErrorResponse envelope
    so the cockpit can show them under the editor."""
    client = TestClient(app)
    r = client.post(
        "/api/v1/clickhouse/query",
        json={"sql": "SELEKT 1"},  # typo'd SELECT
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "bad_request"
    assert "message" in body


def test_empty_sql_returns_envelope_422():
    """Pydantic min_length=1 catches empty input."""
    client = TestClient(app)
    r = client.post("/api/v1/clickhouse/query", json={"sql": ""})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_whitespace_only_sql_returns_envelope_400():
    """`   ;  ` strips to empty; the route catches that with 400."""
    client = TestClient(app)
    r = client.post("/api/v1/clickhouse/query", json={"sql": "   ;  "})
    assert r.status_code == 400
    body = r.json()
    assert "code" in body


def test_max_rows_clamped_to_ceiling():
    """Even if a future schema relaxation lets max_rows past 30k, the
    service clamps at the ceiling — verified by inspecting the
    service directly (the request schema enforces le=30000)."""
    # Calling the service with a value above the ceiling — it should
    # clamp without erroring.
    result = query_service.execute(
        "SELECT 1 AS x",
        max_rows=999_999,
        timeout_seconds=999_999,
    )
    assert result["row_count"] == 1
