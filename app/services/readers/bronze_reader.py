"""
BronzeReader — read service for `bronze.{provider}_minute` Iceberg tables.

This is the **CH-independent path**: agents and ML pipelines reading
historical data go through this service and never touch ClickHouse.
Reproducibility-critical training runs depend on bronze being readable
from S3 + Glue alone.

The reader is a thin wrapper around PyIceberg `Table.scan(...)` plus a
Pydantic conversion. Filters push down to Iceberg (partition pruning by
month + row-group min/max skip by symbol/timestamp), so a per-symbol
month-bounded query scans tens of MB out of ~36 GB.

Design intent (see `docs/standards/platform_design.md`):

  - Contract-first. `get_bars(...) -> list[BronzeBar]` is the only thing
    callers depend on. The Pydantic shape in `schemas.py` is what MCP
    tools and HTTP routes will both surface.
  - Pure read path. No writes, no side effects, no global state beyond
    the catalog handle. Safe to call from any process, any thread.
  - Provider-agnostic. Bronze tables are per-provider, but the API isn't —
    a caller asks for `provider="polygon"` or `"schwab"` and the reader
    picks the table.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan
from pyiceberg.table import Table

from app.config import settings
from app.services.iceberg_catalog import get_catalog
from app.services.readers.schemas import BronzeBar

# Default `since` window for list_symbols when the caller doesn't provide
# one. Picked to balance "covers most active tickers" (a stock that hasn't
# traded in 30 days is probably not in the agent's universe of interest)
# against scan cost (~30M rows on production bronze). Override per-call.
_DEFAULT_SYMBOLS_LOOKBACK = timedelta(days=30)

logger = logging.getLogger(__name__)


# Provider → bronze table-name mapping. Adding a provider = one line here
# plus the table existing in Glue. The provider name a caller passes is the
# **logical** provider ("polygon"), not the source tag ("polygon-flatfiles" /
# "polygon-rest"); the source column stays in the row for granular consumers.
_PROVIDER_TABLE = {
    "polygon": "polygon_minute",
    "schwab": "schwab_minute",
}


def _table_id(provider: str) -> str:
    name = _PROVIDER_TABLE.get(provider)
    if name is None:
        supported = ", ".join(sorted(_PROVIDER_TABLE))
        raise ValueError(
            f"Unknown provider {provider!r}. Supported: {supported}."
        )
    return f"{settings.iceberg_glue_database}.{name}"


def _ensure_utc(ts: datetime) -> datetime:
    """
    Normalize a datetime to tz-aware UTC.

    Iceberg's `timestamptz` filter expects UTC-aware datetimes. Naive
    datetimes from callers are treated as UTC (a documented contract;
    callers passing local-time naive datetimes will get wrong rows back
    by their own choice).
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


class BronzeReader:
    """
    Read interface over bronze minute-bar tables.

    Construct via `from_settings()` for the common production path; pass
    a catalog directly to the constructor in tests so you can substitute
    a temp/in-memory catalog.
    """

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    @classmethod
    def from_settings(cls) -> "BronzeReader":
        return cls(get_catalog())

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    def _load_table(self, provider: str) -> Optional[Table]:
        """
        Load the Iceberg table for `provider`, or return None if it
        doesn't exist yet. Raises `ValueError` for an unknown provider
        — that's a programming error, not a data condition.
        """
        table_id = _table_id(provider)
        try:
            return self._catalog.load_table(table_id)
        except NoSuchTableError:
            logger.warning("bronze_reader: table %s does not exist yet", table_id)
            return None

    # ─────────────────────────────────────────────────────────────────
    # Read methods
    # ─────────────────────────────────────────────────────────────────

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        provider: str = "polygon",
        limit: Optional[int] = None,
    ) -> list[BronzeBar]:
        """
        Return all bars for `symbol` in [start, end) from the named
        provider's bronze table. End is exclusive (half-open interval) —
        matches the convention used elsewhere in the codebase and avoids
        the "minute zero of next day" ambiguity at day boundaries.

        Empty result is `[]`, not an exception. Unknown provider IS an
        exception (programming error, not a data condition).

        `limit` clamps the result size — useful when an agent asks for
        a wide window and we want to bound payload size. If hit, the
        most recent bars are returned (sort: timestamp ASC then truncate
        from the start). Default unlimited.
        """
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        if end_utc <= start_utc:
            return []

        table = self._load_table(provider)
        if table is None:
            return []

        row_filter = And(
            EqualTo("symbol", symbol),
            GreaterThanOrEqual("timestamp", start_utc.isoformat()),
            LessThan("timestamp", end_utc.isoformat()),
        )

        # `selected_fields` is a performance pin — we only need the
        # BronzeBar columns, no need to materialize ingestion_* metadata.
        scan = table.scan(
            row_filter=row_filter,
            selected_fields=(
                "symbol",
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "vwap",
                "trade_count",
                "source",
            ),
        )

        arrow = scan.to_arrow()
        if arrow.num_rows == 0:
            return []

        # Sort timestamp ascending — Iceberg doesn't guarantee scan-order
        # matches sort spec when multiple files are involved.
        import pyarrow.compute as pc

        sort_idx = pc.sort_indices(arrow, sort_keys=[("timestamp", "ascending")])
        arrow = arrow.take(sort_idx)

        if limit is not None and arrow.num_rows > limit:
            arrow = arrow.slice(arrow.num_rows - limit, limit)

        return [
            BronzeBar(
                symbol=row["symbol"],
                timestamp=row["timestamp"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                vwap=row["vwap"],
                trade_count=row["trade_count"],
                source=row["source"] or provider,
            )
            for row in arrow.to_pylist()
        ]

    def list_symbols(
        self,
        *,
        provider: str = "polygon",
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[str]:
        """
        Return sorted, distinct symbols with at least one bar since
        `since` (UTC). Defaults to the last 30 days when `since` is
        None — universe discovery for screeners and agents.

        Filters out null/empty symbols (data-quality boundary, same as
        bronze sink does on write). If the table doesn't exist yet,
        returns `[]`. Unknown provider raises `ValueError`.

        Cost note: scans only the `symbol` column over the requested
        window, with monthly-partition pruning on `timestamp`. A
        30-day window against 2B-row bronze materializes ~500 MB of
        symbol strings; deduplication via pyarrow.compute is in-memory
        but bounded.
        """
        # Trigger `_PROVIDER_TABLE` validation early — `ValueError` for
        # unknown provider is a programming error and should fail loudly.
        _table_id(provider)
        table = self._load_table(provider)
        if table is None:
            return []

        since_utc = _ensure_utc(
            since if since is not None else datetime.now(timezone.utc) - _DEFAULT_SYMBOLS_LOOKBACK
        )

        arrow = table.scan(
            row_filter=GreaterThanOrEqual("timestamp", since_utc.isoformat()),
            selected_fields=("symbol",),
        ).to_arrow()
        if arrow.num_rows == 0:
            return []

        import pyarrow.compute as pc

        unique = pc.unique(arrow["symbol"]).to_pylist()
        symbols = sorted(s for s in unique if s)
        if limit is not None and limit > 0:
            symbols = symbols[:limit]
        return symbols

    def latest_trading_day(
        self, *, provider: str = "polygon", lookback_days: int = 14
    ) -> Optional[date]:
        """
        Most recent trading day (ET basis) with at least one bar in
        the provider's bronze table. Returns `None` if no rows exist
        in the lookback window.

        Why ET, not UTC: see `docs/standards/data/timezone_et_vs_utc.md`.
        After-hours bars cross midnight UTC, so UTC date misclassifies
        them and would advance the counter early.

        Delegates to `app.services.bronze.gaps.latest_bronze_date` so
        gap-detection and read-service share one source of truth.
        Lazy import keeps this module light at top-level.
        """
        # Trigger `_PROVIDER_TABLE` validation for consistency.
        _table_id(provider)
        table = self._load_table(provider)
        if table is None:
            return None

        from app.services.bronze.gaps import latest_bronze_date

        return latest_bronze_date(table, lookback_days=lookback_days)
