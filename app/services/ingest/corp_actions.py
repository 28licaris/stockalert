"""
Polygon corp-actions → `equities.market_corp_actions` ingest.

The canonical v2 writer for Polygon's corp-actions REST API. Pulls
splits + dividends from `PolygonCorpActionsClient` and upserts the
rows into `equities.market_corp_actions`.

**Pattern note:** This is the corp-actions ingest job — analogous to
`nightly_polygon_refresh.py` for OHLCV. It writes to the lake
namespace directly; no silver build step (silver is being retired
in Phase 1C, `equities.market_corp_actions` is the canonical store).

**Two modes:**

- `backfill_full_history(since)`: one-shot pull when seeding the
  lake. ~50K splits + ~3M dividends since 2003. Wall-clock: minutes
  for splits, ~30-60 min for dividends (bounded by Polygon pagination
  cadence).
- `run_nightly()`: incremental — pull yesterday's announcements.
  Idempotent on re-run via the `(symbol, ex_date, action_type)`
  identifier in Iceberg upsert.

**Architectural guarantees:**
- Writes to `equities.market_corp_actions` only.
- Idempotent: re-running with the same date window produces no
  duplicates (upsert join handles revisions cleanly).
- Reproducibility: every row tagged with `ingestion_ts` +
  `ingestion_run_id` so the audit trail is complete.
- Pure consumer of `PolygonCorpActionsClient` — swap the client
  for a stub in tests.

CV1 schema parity: this writer leaves `raw_payload` NULL. The column
exists in the Iceberg schema so a future enhancement to
`PolygonCorpActionsClient` can capture the raw API JSON without a
schema migration; the writer doesn't fabricate one today.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import pyarrow as pa

from app.providers.polygon_corp_actions import PolygonCorpActionsClient
from app.services.equities.tables import ensure_market_corp_actions
from app.services.iceberg_safe_upsert import chunked_upsert
from app.services.equities.models import CorpAction

logger = logging.getLogger(__name__)


# Arrow schema for `equities.market_corp_actions` — must exactly
# match the Iceberg schema in `app/services/equities/schemas.py`.
# Field order + nullability are load-bearing; PyIceberg uses these
# for schema validation on write. Includes `raw_payload` (nullable
# string, populated NULL by this writer — see module docstring).
_CORP_ACTIONS_ARROW = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("ex_date", pa.date32(), nullable=False),
        pa.field("action_type", pa.string(), nullable=False),
        pa.field("factor", pa.float64(), nullable=True),
        pa.field("cash_amount", pa.float64(), nullable=True),
        pa.field("announced_at", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("source_provider", pa.string(), nullable=False),
        pa.field("raw_payload", pa.string(), nullable=True),
        pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("ingestion_run_id", pa.string(), nullable=True),
    ]
)


class PolygonCorpActionsIngest:
    """Orchestrates Polygon REST → equities.market_corp_actions.

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
    def from_settings(cls) -> "PolygonCorpActionsIngest":
        return cls(client=PolygonCorpActionsClient.from_settings())

    def _get_client(self) -> PolygonCorpActionsClient:
        if self._client is None:
            self._client = PolygonCorpActionsClient.from_settings()
        return self._client

    def _get_table(self):
        if self._table is None:
            self._table = ensure_market_corp_actions()
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

        **Internally chunks by calendar year** to avoid OOM. Earlier
        attempts to pull the full 23-year window in one shot crashed
        silently during the dividend pull (millions of rows held in
        memory at once on residential hardware). Year-chunking holds
        at most ~150K rows in memory per chunk while still producing
        identical output via Iceberg upsert.

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

        total_splits = 0
        total_dividends = 0

        # Iterate by calendar year so each Polygon pagination + each
        # Iceberg upsert holds bounded memory.
        #
        # Logging contract: ALWAYS log the pull count for both splits
        # AND dividends, even when zero. A missing dividend log in
        # production was a tell-tale sign of a silent upsert-time crash
        # (TA-5.0, 2026-05-18) — making 0-row pulls observable closes
        # that gap.
        year = since.year
        end_year = until.year
        while year <= end_year:
            chunk_start = max(date(year, 1, 1), since)
            chunk_end = min(date(year, 12, 31), until)

            chunk_splits = await client.collect_splits(
                since=chunk_start, until=chunk_end,
            )
            logger.info(
                "polygon_corp_actions_ingest: year=%d pulled %d splits",
                year, len(chunk_splits),
            )
            if chunk_splits:
                self._upsert(table, chunk_splits, ingestion_run_id=run_id)
                total_splits += len(chunk_splits)

            chunk_divs = await client.collect_dividends(
                since=chunk_start, until=chunk_end,
            )
            logger.info(
                "polygon_corp_actions_ingest: year=%d pulled %d dividends",
                year, len(chunk_divs),
            )
            if chunk_divs:
                self._upsert(table, chunk_divs, ingestion_run_id=run_id)
                total_dividends += len(chunk_divs)

            # Year-completed marker — makes it trivial to grep
            # `year_complete=2022` in operator logs + know exactly
            # how far the loop got before any silent crash.
            logger.info(
                "polygon_corp_actions_ingest: year_complete=%d "
                "running_total splits=%d dividends=%d",
                year, total_splits, total_dividends,
            )
            year += 1

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        logger.info(
            "polygon_corp_actions_ingest: full backfill done — "
            "splits=%d dividends=%d duration=%.1fs",
            total_splits, total_dividends, duration,
        )
        return {
            "ingestion_run_id": run_id,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "splits_written": total_splits,
            "dividends_written": total_dividends,
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
        equities.market_corp_actions schema.

        Stamps every row with `ingestion_ts` (defaults to now-UTC) and
        `ingestion_run_id` so the audit trail is intact. `raw_payload`
        is populated NULL — see module docstring.
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
            "raw_payload": [None for _ in rows],
            "ingestion_ts": [ingestion_ts for _ in rows],
            "ingestion_run_id": [ingestion_run_id for _ in rows],
        }
        return pa.Table.from_pydict(arrays, schema=_CORP_ACTIONS_ARROW)

    @staticmethod
    def _dedupe_actions(actions: list[CorpAction]) -> tuple[list[CorpAction], int]:
        """Collapse rows with the same `(symbol, ex_date, action_type)`.

        Real-world finding (TA-5.0 live verification, 2026-05-17):
        Polygon's /dividends endpoint can return **multiple cash
        dividends on the same ex_date** for a single ticker —
        typically a regular cash dividend + a special/variable
        distribution announced together. Both rows arrive labeled
        with the same `dividend_type=CD`, producing identical
        identifiers in our schema.

        For adjustment math, what matters is the **total cash
        distributed** on the ex_date — silver consumers want one row
        per identifier with the combined amount. We sum:

          - cash_amount: sum across duplicates
          - factor: take max (splits never duplicate; defensive)
          - announced_at: take max (latest announcement wins)
          - source_provider: first

        Returns (deduplicated_list, n_collapsed).
        """
        if not actions:
            return [], 0

        # Group by identifier
        from collections import defaultdict

        groups: dict[tuple, list[CorpAction]] = defaultdict(list)
        for a in actions:
            key = (a.symbol, a.ex_date, a.action_type)
            groups[key].append(a)

        deduped: list[CorpAction] = []
        n_collapsed = 0
        for key, rows in groups.items():
            if len(rows) == 1:
                deduped.append(rows[0])
                continue
            n_collapsed += len(rows) - 1
            # Combine the duplicates.
            cash_amounts = [r.cash_amount for r in rows if r.cash_amount is not None]
            factors = [r.factor for r in rows if r.factor is not None]
            announced = [r.announced_at for r in rows if r.announced_at is not None]
            deduped.append(
                CorpAction(
                    symbol=rows[0].symbol,
                    ex_date=rows[0].ex_date,
                    action_type=rows[0].action_type,
                    factor=max(factors) if factors else None,
                    cash_amount=sum(cash_amounts) if cash_amounts else None,
                    announced_at=max(announced) if announced else None,
                    source_provider=rows[0].source_provider,
                )
            )
        return deduped, n_collapsed

    @classmethod
    def _upsert(
        cls,
        table,
        actions: list[CorpAction],
        *,
        ingestion_run_id: str,
    ) -> None:
        """Write actions to bronze via Iceberg `upsert`.

        Dedupes input first (same-day same-symbol same-kind events get
        their cash_amount summed; see `_dedupe_actions`). Then writes
        with PyIceberg's `upsert`, which uses the identifier fields
        (symbol, ex_date, action_type) for the join. When matched,
        updates non-key columns (handles Polygon revising a prior
        announcement). When not matched, inserts.

        Goes through `chunked_upsert` to dodge PyIceberg's multi-column
        predicate-tree SIGBUS. See `app/services/iceberg_safe_upsert.py`
        for the root-cause analysis. The outer `backfill_full_history`
        is idempotent so partial chunk-progress on a crash heals on
        re-run.
        """
        if not actions:
            return
        deduped, n_collapsed = cls._dedupe_actions(actions)
        if n_collapsed > 0:
            logger.info(
                "polygon_corp_actions_ingest: collapsed %d duplicate "
                "(symbol, ex_date, action_type) rows by summing cash_amount",
                n_collapsed,
            )
        arrow = cls._actions_to_arrow(deduped, ingestion_run_id=ingestion_run_id)
        result = chunked_upsert(
            table, arrow, log_label="equities.market_corp_actions",
        )
        logger.info(
            "polygon_corp_actions_ingest: upsert complete "
            "rows_updated=%d rows_inserted=%d (post-dedup rows=%d chunks=%d)",
            result.rows_updated, result.rows_inserted,
            len(deduped), result.chunks_committed,
        )
