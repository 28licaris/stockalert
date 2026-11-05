"""StateSnapshot: back up everything in ClickHouse that the lake cannot rebuild.

Bars are rebuilt from the lake (scripts/rebuild_ch_from_lake.py). Everything
else is state that exists only in ClickHouse: the stream universe, strategy
definitions, the forward paper-trading record, watchlists, run history, journal,
options history, assistant data. This exports every such table to S3 as Parquet
plus a manifest, and restores them.

Safe by default: a table is exported UNLESS it is on the short, explicit
``EXCLUDED_TABLES`` list of large rebuildable bar tables. A table added next
month is therefore backed up automatically; nothing has to remember it.

Guarantees:
  * ``latest.json`` is written LAST and only for a complete snapshot, so a
    partial export can never become the one a restore picks.
  * Every object is verified by stored size right after upload; restore verifies
    each object's sha256 before inserting.
  * A table that fails is recorded in ``manifest.failed`` and the export reports
    incomplete; one bad table does not stop the others.
  * Restore never silently mixes data: a non-empty table is skipped unless
    ``mode="replace"`` is explicit.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from app.services.lake_integrity.contract import ChStateClient, ObjectStore
from app.services.lake_integrity.schemas import (
    CoverageRow,
    FileEntry,
    RestoreReport,
    SnapshotManifest,
    TableEntry,
    TableRestore,
)

logger = logging.getLogger(__name__)

PREFIX = "ch_state"

# Large bar tables that are rebuilt from the lake or are research scratch.
# Everything NOT listed here is backed up.
EXCLUDED_TABLES = frozenset({"ohlcv_1m", "futures_ohlcv_1m", "ohlcv_1m_candidates", "ohlcv_hourly"})

# Bar tables whose per-symbol coverage the manifest records (drives the rebuild
# windows and the post-rebuild verification).
COVERAGE_TABLES = ("ohlcv_1m", "futures_ohlcv_1m")

# A new snapshot whose table holds less than this fraction of the previous
# snapshot's rows (for a table that had at least SHRINK_MIN_ROWS) is not
# published as ``latest``: it almost certainly came from an empty or half-built
# ClickHouse, and publishing it would let a bad backup replace a good one.
SHRINK_FLOOR = 0.5
SHRINK_MIN_ROWS = 100

# Files outside ClickHouse that belong to the same record (paper-trading alert history).
_FILE_SUFFIXES = (".json", ".jsonl")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_view(engine: str) -> bool:
    return "View" in engine or engine in {"Memory", "Null"}


class StateSnapshot:
    def __init__(
        self,
        *,
        ch: ChStateClient,
        store: ObjectStore,
        database: str,
        paper_dir: Optional[Path] = None,
        prefix: str = PREFIX,
    ) -> None:
        self._ch, self._store, self._db = ch, store, database
        self._paper_dir = paper_dir
        self._prefix = prefix.strip("/")

    @classmethod
    def from_settings(cls, paper_dir: Optional[Path] = None) -> "StateSnapshot":
        from app.config import settings
        from app.services.lake_integrity.adapters import ClickHouseStateClient, S3ObjectStore

        return cls(
            ch=ClickHouseStateClient(),
            store=S3ObjectStore(settings.stock_lake_bucket, settings.stock_lake_region),
            database=settings.clickhouse_database,
            paper_dir=paper_dir if paper_dir is not None
            else Path(settings.stockalert_paper_dir) if settings.stockalert_paper_dir
            else Path.cwd() / "data" / "paper",
        )

    # ── export ────────────────────────────────────────────────────────
    def export(self, *, now: Optional[datetime] = None, force: bool = False) -> SnapshotManifest:
        now = now or datetime.now(timezone.utc)
        sid = now.strftime("%Y%m%dT%H%M%SZ")
        base = f"{self._prefix}/{sid}"
        tables: list[TableEntry] = []
        failed: dict[str, str] = {}
        excluded: list[str] = []
        for name, engine in sorted(self._ch.list_tables(self._db)):
            if name in EXCLUDED_TABLES:
                excluded.append(name)
                continue
            if _is_view(engine) or name.endswith("_staging"):
                continue
            try:
                tables.append(self._export_table(base, name, engine))
            except Exception as exc:  # noqa: BLE001 - one table must not stop the rest
                logger.exception("state_snapshot: table %s failed", name)
                failed[name] = f"{type(exc).__name__}: {exc}"
        files = self._export_files(base, failed)
        coverage = self._coverage(excluded, failed)
        if not force:
            self._check_shrink(tables, coverage, failed)
        manifest = SnapshotManifest(
            snapshot_id=sid, created_at=now, database=self._db,
            tables=tables, files=files, excluded=excluded, coverage=coverage, failed=failed,
        )
        self._store.put(f"{base}/manifest.json", manifest.model_dump_json(indent=2).encode())
        if manifest.complete:
            self._store.put(f"{self._prefix}/latest.json", json.dumps({"snapshot_id": sid}).encode())
        logger.info(
            "state_snapshot: %s tables=%d rows=%d files=%d excluded=%s failed=%d complete=%s",
            sid, len(tables), sum(t.rows for t in tables), len(files), sorted(excluded),
            len(failed), manifest.complete,
        )
        return manifest

    def _check_shrink(
        self, tables: list[TableEntry], coverage: dict[str, list[CoverageRow]], failed: dict[str, str]
    ) -> None:
        """Flag a snapshot that is much smaller than the last published one."""
        prev_id = self.latest_id()
        if not prev_id:
            return
        prev = self.manifest(prev_id)
        now_rows = {t.name: t.rows for t in tables}
        for t in prev.tables:
            if t.rows >= SHRINK_MIN_ROWS and now_rows.get(t.name, 0) < t.rows * SHRINK_FLOOR:
                failed[f"shrink:{t.name}"] = (
                    f"{now_rows.get(t.name, 0)} rows now vs {t.rows} in snapshot {prev_id} "
                    f"(below {int(SHRINK_FLOOR * 100)}%); refusing to publish. Re-run with force if intended."
                )
        for table, prev_cov in prev.coverage.items():
            if len(prev_cov) >= 20 and len(coverage.get(table, [])) < len(prev_cov) * SHRINK_FLOOR:
                failed[f"shrink:coverage:{table}"] = (
                    f"{len(coverage.get(table, []))} symbols now vs {len(prev_cov)} in snapshot {prev_id}"
                )

    def _coverage(self, excluded: list[str], failed: dict[str, str]) -> dict[str, list[CoverageRow]]:
        out: dict[str, list[CoverageRow]] = {}
        for table in COVERAGE_TABLES:
            if table not in excluded:
                continue
            try:
                out[table] = [
                    CoverageRow(symbol=sym, first=a, last=b, rows=n)
                    for sym, a, b, n in self._ch.coverage(self._db, table)
                ]
            except Exception as exc:  # noqa: BLE001
                logger.exception("state_snapshot: coverage of %s failed", table)
                failed[f"coverage:{table}"] = f"{type(exc).__name__}: {exc}"
        return out

    def _export_table(self, base: str, name: str, engine: str) -> TableEntry:
        arrow = self._ch.read_table(self._db, name, final="Replacing" in engine)
        buf = io.BytesIO()
        pq.write_table(arrow, buf, compression="zstd")
        data = buf.getvalue()
        key = f"{base}/tables/{name}.parquet"
        self._store.put(key, data)
        stored = self._store.size(key)
        if stored != len(data):
            raise RuntimeError(f"stored size {stored} != uploaded {len(data)} for {key}")
        logger.info("state_snapshot: table_complete=%s rows=%d bytes=%d", name, arrow.num_rows, len(data))
        return TableEntry(
            name=name, rows=arrow.num_rows, bytes=len(data), sha256=_sha256(data),
            ddl=self._ch.create_ddl(self._db, name), key=key,
        )

    def _export_files(self, base: str, failed: dict[str, str]) -> list[FileEntry]:
        out: list[FileEntry] = []
        d = self._paper_dir
        if d is None or not d.is_dir():
            return out
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix not in _FILE_SUFFIXES:
                continue
            try:
                data = p.read_bytes()
                key = f"{base}/files/paper/{p.name}"
                self._store.put(key, data)
                if self._store.size(key) != len(data):
                    raise RuntimeError(f"stored size mismatch for {key}")
                out.append(FileEntry(name=p.name, bytes=len(data), sha256=_sha256(data), key=key))
            except Exception as exc:  # noqa: BLE001
                logger.exception("state_snapshot: file %s failed", p.name)
                failed[f"file:{p.name}"] = f"{type(exc).__name__}: {exc}"
        return out

    # ── read side ─────────────────────────────────────────────────────
    def latest_id(self) -> Optional[str]:
        key = f"{self._prefix}/latest.json"
        if self._store.size(key) is None:
            return None
        return json.loads(self._store.get(key))["snapshot_id"]

    def manifest(self, snapshot_id: str = "latest") -> SnapshotManifest:
        sid = self.latest_id() if snapshot_id == "latest" else snapshot_id
        if not sid:
            raise LookupError("no complete state snapshot exists yet")
        return SnapshotManifest.model_validate_json(self._store.get(f"{self._prefix}/{sid}/manifest.json"))

    # ── restore ───────────────────────────────────────────────────────
    def restore(
        self,
        snapshot_id: str = "latest",
        *,
        mode: Literal["empty_only", "replace"] = "empty_only",
        tables: Optional[list[str]] = None,
        target_database: Optional[str] = None,
        restore_files: bool = False,
        overwrite_files: bool = False,
    ) -> RestoreReport:
        m = self.manifest(snapshot_id)
        dst = target_database or m.database
        report = RestoreReport(snapshot_id=m.snapshot_id, tables=[])
        if dst != m.database:
            self._ch.command(f"CREATE DATABASE IF NOT EXISTS {dst}")
        for t in m.tables:
            if tables is not None and t.name not in tables:
                continue
            report.tables.append(self._restore_table(t, m.database, dst, mode))
        if restore_files:
            self._restore_files(m, report, overwrite_files)
        logger.info(
            "state_snapshot: restore %s into %s mode=%s ok=%s tables=%s",
            m.snapshot_id, dst, mode, report.ok,
            {t.name: t.status for t in report.tables},
        )
        return report

    def _restore_table(self, t: TableEntry, src_db: str, dst_db: str, mode: str) -> TableRestore:
        try:
            ddl = self._ddl_for_target(t.ddl, src_db, dst_db)
            self._ch.command(ddl)
            if self._ch.row_count(dst_db, t.name) > 0:
                if mode != "replace":
                    return TableRestore(name=t.name, status="skipped_nonempty")
                self._ch.command(f"TRUNCATE TABLE {dst_db}.{t.name}")
            data = self._store.get(t.key)
            if _sha256(data) != t.sha256:
                raise RuntimeError(f"checksum mismatch for {t.key}")
            arrow = pq.read_table(io.BytesIO(data))
            if arrow.num_rows != t.rows:
                raise RuntimeError(f"{t.key} holds {arrow.num_rows} rows, manifest says {t.rows}")
            if arrow.num_rows:
                self._ch.insert(dst_db, t.name, arrow)
            after = self._ch.row_count(dst_db, t.name)
            # The table was empty (or truncated) and we inserted exactly t.rows. A table
            # with a TTL may legitimately expire rows that were past it but not yet
            # merged away when exported, so it may hold fewer, never more and never none.
            has_ttl = "\nTTL " in t.ddl or " TTL " in t.ddl
            ok = after == t.rows or (has_ttl and 0 < after <= t.rows)
            if not ok:
                raise RuntimeError(f"after insert {dst_db}.{t.name} has {after} rows, expected {t.rows}")
            return TableRestore(name=t.name, status="restored", rows=arrow.num_rows)
        except Exception as exc:  # noqa: BLE001
            logger.exception("state_snapshot: restore of %s failed", t.name)
            return TableRestore(name=t.name, status="error", error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _ddl_for_target(ddl: str, src_db: str, dst_db: str) -> str:
        head = f"CREATE TABLE {src_db}."
        if not ddl.startswith(head):
            raise ValueError(f"unexpected DDL (expected it to start with {head!r}): {ddl[:60]!r}")
        return f"CREATE TABLE IF NOT EXISTS {dst_db}." + ddl[len(head):]

    def _restore_files(self, m: SnapshotManifest, report: RestoreReport, overwrite: bool) -> None:
        d = self._paper_dir
        if d is None:
            report.errors.append("restore_files requested but no paper_dir is configured")
            return
        d.mkdir(parents=True, exist_ok=True)
        for f in m.files:
            target = d / f.name
            if target.exists() and not overwrite:
                report.files_skipped += 1
                continue
            data = self._store.get(f.key)
            if _sha256(data) != f.sha256:
                report.errors.append(f"checksum mismatch for {f.key}")
                continue
            target.write_bytes(data)
            report.files_restored += 1

    # ── retention ─────────────────────────────────────────────────────
    def prune(self, *, keep: int = 14) -> list[str]:
        """Delete all but the newest ``keep`` snapshots; the one ``latest.json``
        points at is never deleted. Returns the snapshot ids removed."""
        ids = sorted({k.split("/")[1] for k in self._store.list(f"{self._prefix}/") if k.count("/") >= 2})
        protect = self.latest_id()
        doomed = [s for s in ids[:-keep] if s != protect] if keep > 0 else []
        for sid in doomed:
            for key in self._store.list(f"{self._prefix}/{sid}/"):
                self._store.delete(key)
        if doomed:
            logger.info("state_snapshot: pruned %d old snapshot(s): %s", len(doomed), doomed)
        return doomed
