"""ClickHouse ad-hoc query service. See README.md."""
from app.services.clickhouse_query.query_service import (
    ClickHouseQueryService,
    query_service,
)

__all__ = ["ClickHouseQueryService", "query_service"]
