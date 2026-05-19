"""
Silver corp-actions build: bronze → silver merger with provider precedence.

Reads every configured `bronze.{provider}_corp_actions` table, merges
their rows with provider precedence (default: `polygon > schwab >
...`), and upserts the canonical result into `silver.corp_actions`.

**This is the medallion bronze→silver step for corp-actions.** Mirror
of the planned OHLCV silver build (silver_layer_plan.md §3). When a
second corp-actions provider is added later, it's a one-line
config change to `silver_provider_precedence` — this build picks
it up automatically.

**Idempotent.** Re-running over the same window produces the same
silver rows (Iceberg upsert on the identifier
`(symbol, ex_date, action_type)` handles repeats).

**Modes:**
- `run_full()`: merge all available bronze data into silver. Used
  after the initial bronze backfill (TA-5.0 step 5b).
- `run_since(date)`: merge bronze rows where `ex_date >= since` into
  silver. Used for nightly incremental builds.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import pyarrow as pa
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import GreaterThanOrEqual

from app.config import settings
from app.services.iceberg_catalog import get_catalog
from app.services.iceberg_safe_upsert import chunked_upsert
from app.services.silver.schemas import silver_table_id
from app.services.silver.tables import ensure_silver_corp_actions

logger = logging.getLogger(__name__)


# Arrow schema for `silver.corp_actions` — identical column shape
# to bronze.polygon_corp_actions per the design (silver_layer_plan §4.1).
# The medallion difference is process (provider precedence + canonical
# dedup), not shape.
_SILVER_CORP_ACTIONS_ARROW = pa.schema(
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


class SilverCorpActionsBuild:
    """Orchestrates bronze.{provider}_corp_actions → silver.corp_actions.

    Provider list comes from `settings.silver_provider_precedence`.
    Per [silver_layer_plan §14.1](../../../../docs/silver_layer_plan.md),
    default is `polygon > schwab` (polygon wins per
    (symbol, ex_date, action_type); schwab fills cells polygon doesn't
    cover). Adding a new provider = one bronze table + one comma in
    the env var + (optional) update to the default in app/config.py.

    Construct via `from_settings()`; pass explicit dependencies for
    tests.
    """

    def __init__(
        self,
        *,
        catalog=None,
        silver_table=None,
        provider_precedence: Optional[list[str]] = None,
    ) -> None:
        self._catalog = catalog
        self._silver_table = silver_table
        self._provider_precedence = provider_precedence

    @classmethod
    def from_settings(cls) -> "SilverCorpActionsBuild":
        precedence = [
            p.strip()
            for p in (settings.silver_provider_precedence or "").split(",")
            if p.strip()
        ]
        if not precedence:
            raise ValueError(
                "silver_provider_precedence is empty. Set "
                "SILVER_PROVIDER_PRECEDENCE in .env (default: 'polygon,schwab')."
            )
        return cls(provider_precedence=precedence)

    def _get_catalog(self):
        if self._catalog is None:
            self._catalog = get_catalog()
        return self._catalog

    def _get_silver_table(self):
        if self._silver_table is None:
            self._silver_table = ensure_silver_corp_actions(self._get_catalog())
        return self._silver_table

    def _get_provider_precedence(self) -> list[str]:
        if self._provider_precedence is None:
            self._provider_precedence = [
                p.strip()
                for p in (settings.silver_provider_precedence or "").split(",")
                if p.strip()
            ]
        return self._provider_precedence

    # ─────────────────────────────────────────────────────────────────
    # Public modes
    # ─────────────────────────────────────────────────────────────────

    def run_full(self) -> dict:
        """Merge all bronze.{provider}_corp_actions rows into silver.

        Used after the initial bronze backfill (TA-5.0 step 5b) to
        seed silver with the full historical archive. Subsequent
        runs are typically `run_since(yesterday)` for incrementals.
        """
        return self._build(since=None)

    def run_since(self, since: date) -> dict:
        """Merge bronze corp-actions where `ex_date >= since` into silver.

        Idempotent — Iceberg upsert on (symbol, ex_date, action_type)
        handles repeats. Used for nightly incremental builds; pass
        `yesterday` to catch the day's announcements.
        """
        return self._build(since=since)

    def run_nightly(self) -> dict:
        """Convenience: incremental build over yesterday's ex_date window."""
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        return self.run_since(yesterday)

    # ─────────────────────────────────────────────────────────────────
    # Build pipeline
    # ─────────────────────────────────────────────────────────────────

    def _build(self, *, since: Optional[date]) -> dict:
        run_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc)
        precedence = self._get_provider_precedence()

        logger.info(
            "silver_corp_actions_build: starting run_id=%s since=%s precedence=%s",
            run_id, since.isoformat() if since else "all", precedence,
        )

        # 1. Read each bronze.{provider}_corp_actions table, in precedence
        #    order. Missing tables are skipped silently (a provider not
        #    yet onboarded is a normal state).
        per_provider_rows: list[tuple[str, pa.Table]] = []
        for provider in precedence:
            table_name = f"{provider}_corp_actions"
            arrow = self._read_bronze(table_name, since=since)
            if arrow is None:
                logger.info(
                    "silver_corp_actions_build: provider=%s table missing or "
                    "empty; skipping",
                    provider,
                )
                continue
            logger.info(
                "silver_corp_actions_build: provider=%s read %d rows",
                provider, arrow.num_rows,
            )
            per_provider_rows.append((provider, arrow))

        if not per_provider_rows:
            logger.info("silver_corp_actions_build: no bronze rows to merge; done")
            return {
                "ingestion_run_id": run_id,
                "since": since.isoformat() if since else "all",
                "providers_read": [],
                "rows_merged": 0,
                "duration_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
            }

        # 2. Merge with provider precedence. First provider wins each
        #    (symbol, ex_date, action_type); later providers fill cells
        #    the higher-precedence provider doesn't cover.
        merged = self._merge_with_precedence(per_provider_rows)
        logger.info(
            "silver_corp_actions_build: merged %d rows from %d providers",
            merged.num_rows, len(per_provider_rows),
        )

        # 3. Re-stamp ingestion metadata for this build run. The
        #    ingestion_run_id on the silver rows tags this BUILD,
        #    not the upstream bronze ingest (which is preserved in
        #    bronze's own ingestion_run_id column).
        merged = self._restamp_ingestion(merged, run_id=run_id)

        # 4. Write to silver. Two paths:
        #    - APPEND (empty target): single fast commit, no merge
        #      logic, no predicate tree. ~30-60x faster than upsert
        #      for big initial backfills. Same TA-5.1.12 optimization
        #      we apply to silver.ohlcv_1m.
        #    - chunked_upsert (non-empty target): identifier join on
        #      (symbol, ex_date, action_type). Routed through
        #      chunked_upsert to dodge PyIceberg's multi-column
        #      predicate-tree SIGBUS (see iceberg_safe_upsert.py).
        silver_table = self._get_silver_table()
        target_empty = self._silver_table_empty(silver_table)
        if target_empty:
            logger.info(
                "silver_corp_actions_build: target is empty; using single "
                "append (fast path, no merge logic). rows=%d",
                merged.num_rows,
            )
            silver_table.append(merged)
            rows_updated = 0
            rows_inserted = merged.num_rows
            chunks_committed = 1
        else:
            logger.info(
                "silver_corp_actions_build: target has existing rows; "
                "using chunked upsert path. rows=%d", merged.num_rows,
            )
            result = chunked_upsert(
                silver_table, merged, log_label="silver.corp_actions",
            )
            rows_updated = result.rows_updated
            rows_inserted = result.rows_inserted
            chunks_committed = result.chunks_committed

        logger.info(
            "silver_corp_actions_build: silver write complete "
            "rows_updated=%d rows_inserted=%d chunks=%d "
            "(write_strategy=%s)",
            rows_updated, rows_inserted, chunks_committed,
            "append" if target_empty else "upsert",
        )

        return {
            "ingestion_run_id": run_id,
            "since": since.isoformat() if since else "all",
            "providers_read": [p for p, _ in per_provider_rows],
            "rows_merged": merged.num_rows,
            "rows_updated": rows_updated,
            "rows_inserted": rows_inserted,
            "write_strategy": "append" if target_empty else "upsert",
            "duration_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
        }

    @staticmethod
    def _silver_table_empty(silver_table) -> bool:
        """True iff silver.corp_actions has no current snapshot or 0 rows.

        Mirrors the SilverOhlcvBuild._silver_tables_empty() pattern
        from TA-5.1.12. Fail-safe: any error treats the target as
        non-empty (upsert is always correct; append is only safe on
        a known-empty target).
        """
        try:
            snap = silver_table.current_snapshot()
        except Exception:
            return False
        if snap is None:
            return True
        try:
            summ = snap.summary
            summ_map = (
                summ.additional_properties
                if hasattr(summ, "additional_properties") else dict(summ)
            )
            return int(summ_map.get("total-records", "0")) == 0
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────────────
    # Bronze reads
    # ─────────────────────────────────────────────────────────────────

    def _read_bronze(
        self,
        table_name: str,
        *,
        since: Optional[date],
    ) -> Optional[pa.Table]:
        """Read a bronze.{provider}_corp_actions table to PyArrow.

        Returns None if the table doesn't exist (provider not yet
        onboarded) or has no rows in the window. Otherwise returns
        the full row set as an Arrow Table.
        """
        catalog = self._get_catalog()
        full_id = silver_table_id(table_name)  # same naming pattern, flat DB

        try:
            table = catalog.load_table(full_id)
        except NoSuchTableError:
            return None

        scan = table.scan()
        if since is not None:
            scan = scan.filter(GreaterThanOrEqual("ex_date", since))

        arrow = scan.to_arrow()
        if arrow.num_rows == 0:
            return None
        return arrow

    # ─────────────────────────────────────────────────────────────────
    # Merge step (provider precedence)
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_with_precedence(
        per_provider_rows: list[tuple[str, pa.Table]],
    ) -> pa.Table:
        """Merge per-provider corp-action rows with precedence.

        For each identifier `(symbol, ex_date, action_type)`, the
        first provider in the list that has a row wins. Later
        providers fill cells the higher-precedence ones don't cover.

        Returns one combined PyArrow Table matching the silver
        schema. Uses Python sets / dicts for the merge — corp-actions
        volume is small enough (~3M rows total full history) that
        this is fast; if it ever becomes hot, swap for PyArrow
        compute or a join.
        """
        # Build a dict keyed by identifier. Iterate providers in
        # precedence order; first writer wins.
        merged: dict[tuple, dict] = {}
        for provider, arrow in per_provider_rows:
            for row in arrow.to_pylist():
                key = (row["symbol"], row["ex_date"], row["action_type"])
                if key in merged:
                    continue  # higher-precedence provider already claimed this cell
                merged[key] = row

        if not merged:
            return _empty_silver_arrow()

        rows = list(merged.values())
        arrays = {col: [r[col] for r in rows] for col in _SILVER_CORP_ACTIONS_ARROW.names}
        return pa.Table.from_pydict(arrays, schema=_SILVER_CORP_ACTIONS_ARROW)

    # ─────────────────────────────────────────────────────────────────
    # Audit metadata
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _restamp_ingestion(arrow: pa.Table, *, run_id: str) -> pa.Table:
        """Replace ingestion_ts + ingestion_run_id with this build's values.

        Bronze rows carry the ingest job's run_id; silver rows should
        carry the BUILD job's run_id. This makes silver provenance
        traceable to which silver_build invocation produced it (while
        bronze provenance remains traceable in bronze's own column).
        """
        if arrow.num_rows == 0:
            return arrow
        n = arrow.num_rows
        now = datetime.now(timezone.utc)
        # Drop the bronze ingestion columns and re-add with silver values.
        arrow = arrow.drop(["ingestion_ts", "ingestion_run_id"])
        arrow = arrow.append_column(
            "ingestion_ts",
            pa.array([now] * n, type=pa.timestamp("us", tz="UTC")),
        )
        arrow = arrow.append_column(
            "ingestion_run_id",
            pa.array([run_id] * n, type=pa.string()),
        )
        # Reorder columns to match the silver schema exactly (drop/append
        # otherwise puts them at the end).
        return arrow.select(_SILVER_CORP_ACTIONS_ARROW.names)


def _empty_silver_arrow() -> pa.Table:
    """An empty PyArrow Table matching the silver schema.

    Returned when no providers have data — keeps the build function
    type-stable regardless of input.
    """
    return pa.Table.from_pydict(
        {col: [] for col in _SILVER_CORP_ACTIONS_ARROW.names},
        schema=_SILVER_CORP_ACTIONS_ARROW,
    )
