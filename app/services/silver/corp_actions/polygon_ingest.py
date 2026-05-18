"""
Polygon corp-actions → bronze.polygon_corp_actions ingest.

The bronze-side writer for Polygon's corp-actions REST API. Pulls
splits + dividends from `PolygonCorpActionsClient` and upserts the
raw rows into `bronze.polygon_corp_actions`.

**Pattern note:** This is the bronze ingest job — analogous to
`nightly_polygon_refresh.py` for OHLCV. It writes to bronze
(raw per-provider archive), NEVER directly to silver. The
silver layer's `build.py` then merges all
`bronze.{provider}_corp_actions` tables into the canonical
`silver.corp_actions`. See [silver_layer_plan §4](../../../../docs/silver_layer_plan.md).

**Two modes:**

- `backfill_full_history(since)`: one-shot pull when seeding the
  lake. ~50K splits + ~3M dividends since 2003. Wall-clock: minutes
  for splits, ~30-60 min for dividends (bounded by Polygon pagination
  cadence).
- `run_nightly()`: incremental — pull yesterday's announcements.
  Idempotent on re-run via the `(symbol, ex_date, action_type)`
  identifier in Iceberg upsert.

**Architectural guarantees:**
- Writes to `bronze.polygon_corp_actions` only. Never touches silver.
- Idempotent: re-running with the same date window produces no
  duplicates (upsert join handles revisions cleanly).
- Reproducibility: every row tagged with `ingestion_ts` +
  `ingestion_run_id` so the audit trail is complete.
- Pure consumer of `PolygonCorpActionsClient` — swap the client
  for a stub in tests.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import pyarrow as pa

from app.providers.polygon_corp_actions import PolygonCorpActionsClient
from app.services.bronze.tables import ensure_bronze_polygon_corp_actions
from app.services.silver.schemas import CorpAction

logger = logging.getLogger(__name__)


# Arrow schema for `bronze.polygon_corp_actions` — must exactly match
# the Iceberg schema in `app/services/bronze/schemas.py`. Field order +
# nullability are load-bearing; PyIceberg uses these for schema validation
# on write.
_CORP_ACTIONS_ARROW = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("ex_date", pa.date32(), nullable=False),
        pa.field("action_type", pa.string(), nullable=False),
        pa.field("factor", pa.float64(), nullable=True),
        pa.field("cash_amount", pa.float64(), nullable=True),
        pa.field("announced_at", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("source_provider", pa.string(), nullable=False),
        pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("ingestion_run_id", pa.string(), nullable=True),
    ]
)


class PolygonCorpActionsBronzeIngest:
    """Orchestrates Polygon REST → bronze.polygon_corp_actions.

    Construct via `from_settings()` for production; pass explicit
    `client` for tests with a stubbed Polygon source.
    """

    def __init__(
        self,
        *,
        client: Optional[PolygonCorpActionsClient] = None,
        table=None,  # PyIceberg Table; lazy-loaded if None
    ) -> None:
        self._client = client
        self._table = table

    @classmethod
    def from_settings(cls) -> "PolygonCorpActionsBronzeIngest":
        return cls(client=PolygonCorpActionsClient.from_settings())

    def _get_client(self) -> PolygonCorpActionsClient:
        if self._client is None:
            self._client = PolygonCorpActionsClient.from_settings()
        return self._client

    def _get_table(self):
        if self._table is None:
            self._table = ensure_bronze_polygon_corp_actions()
        return self._table

    # ─────────────────────────────────────────────────────────────────
    # Public modes
    # ─────────────────────────────────────────────────────────────────

    async def backfill_full_history(
        self,
        *,
        since: date = date(2003, 1, 1),
        until: Optional[date] = None,
    ) -> dict:
        """One-shot historical backfill of Polygon corp-actions.

        Pulls every split + dividend from `since` to `until` (default:
        through yesterday). Writes via Iceberg upsert so a partial-
        failure restart is safe — re-running covers the same range
        without duplicates.

        Returns a summary dict:
            {
                "ingestion_run_id": "...",
                "since": "2003-01-01",
                "until": "2026-05-16",
                "splits_written": 52840,
                "dividends_written": 2_945_119,
                "duration_seconds": 2841.5,
            }
        """
        until = until or (datetime.now(timezone.utc).date() - timedelta(days=1))
        run_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc)

        logger.info(
            "polygon_corp_actions_ingest: full backfill since=%s until=%s run_id=%s",
            since, until, run_id,
        )

        client = self._get_client()
        table = self._get_table()

        splits = await client.collect_splits(since=since, until=until)
        logger.info(
            "polygon_corp_actions_ingest: pulled %d splits from Polygon",
            len(splits),
        )
        if splits:
            self._upsert(table, splits, ingestion_run_id=run_id)

        dividends = await client.collect_dividends(since=since, until=until)
        logger.info(
            "polygon_corp_actions_ingest: pulled %d dividends from Polygon",
            len(dividends),
        )
        if dividends:
            self._upsert(table, dividends, ingestion_run_id=run_id)

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        return {
            "ingestion_run_id": run_id,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "splits_written": len(splits),
            "dividends_written": len(dividends),
            "duration_seconds": duration,
        }

    async def run_nightly(self) -> dict:
        """Nightly incremental: pull yesterday's announcements + upsert.

        Idempotent — re-running on the same UTC day produces no
        duplicates (upsert handles existing-row joining).
        """
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        return await self.backfill_full_history(since=yesterday, until=yesterday)

    # ─────────────────────────────────────────────────────────────────
    # Write path — Arrow + PyIceberg upsert
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _actions_to_arrow(
        actions: Iterable[CorpAction],
        *,
        ingestion_run_id: str,
        ingestion_ts: Optional[datetime] = None,
    ) -> pa.Table:
        """Convert a list of CorpAction → PyArrow Table matching the
        bronze.polygon_corp_actions schema.

        Stamps every row with `ingestion_ts` (defaults to now-UTC) and
        `ingestion_run_id` so the audit trail is intact.
        """
        ingestion_ts = ingestion_ts or datetime.now(timezone.utc)

        rows = list(actions)
        arrays = {
            "symbol": [a.symbol for a in rows],
            "ex_date": [a.ex_date for a in rows],
            "action_type": [a.action_type for a in rows],
            "factor": [a.factor for a in rows],
            "cash_amount": [a.cash_amount for a in rows],
            "announced_at": [a.announced_at for a in rows],
            "source_provider": [a.source_provider for a in rows],
            "ingestion_ts": [ingestion_ts for _ in rows],
            "ingestion_run_id": [ingestion_run_id for _ in rows],
        }
        return pa.Table.from_pydict(arrays, schema=_CORP_ACTIONS_ARROW)

    @classmethod
    def _upsert(
        cls,
        table,
        actions: list[CorpAction],
        *,
        ingestion_run_id: str,
    ) -> None:
        """Write actions to bronze via Iceberg `upsert`.

        Uses the identifier fields (symbol, ex_date, action_type) as
        the join condition automatically. When matched, updates
        non-key columns (handles Polygon revising a prior announcement).
        When not matched, inserts.
        """
        if not actions:
            return
        arrow = cls._actions_to_arrow(actions, ingestion_run_id=ingestion_run_id)
        result = table.upsert(arrow)
        logger.info(
            "polygon_corp_actions_ingest: upsert complete "
            "rows_updated=%d rows_inserted=%d",
            result.rows_updated, result.rows_inserted,
        )
