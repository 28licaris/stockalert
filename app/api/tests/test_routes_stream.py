"""HTTP contract for the authoritative stream-universe surface."""
from __future__ import annotations

import pytest

from app.api import routes_stream
from app.main_api import app


@pytest.mark.asyncio
async def test_list_stream_universe_reads_clickhouse_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes_stream.stream_service,
        "list_universe",
        lambda: [
            {
                "symbol": "AAPL",
                "asset_type": "EQUITY",
                "added_at": "2026-06-23T00:00:00Z",
                "added_by": "operator",
                "notes": "",
                "description": "",
            }
        ],
    )

    response = await routes_stream.list_stream_universe()

    assert response.count == 1
    assert response.items[0].symbol == "AAPL"
    assert "bootstrapped" not in response.model_dump()


def test_openapi_exposes_stream_and_not_retired_seed_routes() -> None:
    paths = app.openapi()["paths"]

    assert "/api/v1/stream" in paths
    assert not any(path.startswith("/api/v1/seed") for path in paths)
