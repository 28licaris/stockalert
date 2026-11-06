"""StateSnapshot with an in-memory ClickHouse and object store: export scope,
verification, restore round trip, safety modes, retention."""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pytest

from app.services.lake_integrity.snapshot import EXCLUDED_TABLES, StateSnapshot

T0 = datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc)


class FakeCh:
    def __init__(self, tables: dict[str, tuple[str, pa.Table]] | None = None, db: str = "stocks"):
        # name -> (engine, arrow)
        self.db, self.t, self.commands = db, dict(tables or {}), []
        self.fail_read: set[str] = set()
        self.dbs: dict[str, dict[str, pa.Table]] = {db: {n: a for n, (_, a) in self.t.items()}}
        self.engines = {n: e for n, (e, _) in self.t.items()}

    def list_tables(self, database):
        return [(n, self.engines[n]) for n in self.dbs.get(database, {})]

    def create_ddl(self, database, table):
        return f"CREATE TABLE {database}.{table} (`x` Int64) ENGINE = {self.engines[table]}"

    def read_table(self, database, table, *, final):
        if table in self.fail_read:
            raise RuntimeError("clickhouse exploded")
        return self.dbs[database][table]

    def row_count(self, database, table):
        return self.dbs.get(database, {}).get(table, pa.table({"x": []})).num_rows

    def insert(self, database, table, data):
        cur = self.dbs.setdefault(database, {}).get(table)
        self.dbs[database][table] = data if cur is None or cur.num_rows == 0 else pa.concat_tables([cur, data])

    def coverage(self, database, table):
        if table in self.fail_read:
            raise RuntimeError("coverage exploded")
        return [("AAPL", datetime(2021, 1, 4, tzinfo=timezone.utc), datetime(2026, 11, 4, tzinfo=timezone.utc), 1000),
                ("SPY", datetime(2006, 1, 3, tzinfo=timezone.utc), datetime(2026, 11, 4, tzinfo=timezone.utc), 5000)]

    def command(self, sql):
        self.commands.append(sql)
        if sql.startswith("CREATE TABLE IF NOT EXISTS "):
            qualified = sql.split()[5]
            db, name = qualified.split(".")
            self.dbs.setdefault(db, {}).setdefault(name, pa.table({"x": pa.array([], pa.int64())}))
            self.engines.setdefault(name, "ReplacingMergeTree(v)")
        elif sql.startswith("TRUNCATE TABLE "):
            db, name = sql.split()[2].split(".")
            self.dbs[db][name] = pa.table({"x": pa.array([], pa.int64())})


class MemStore:
    def __init__(self):
        self.o: dict[str, bytes] = {}
        self.truncate_puts = False

    def put(self, key, data):
        self.o[key] = data[:-1] if self.truncate_puts else data

    def get(self, key):
        return self.o[key]

    def size(self, key):
        return len(self.o[key]) if key in self.o else None

    def list(self, prefix):
        return sorted(k for k in self.o if k.startswith(prefix))

    def delete(self, key):
        del self.o[key]


def tbl(n: int) -> pa.Table:
    return pa.table({"x": pa.array(list(range(n)), pa.int64())})


def source(extra: dict | None = None) -> FakeCh:
    t = {
        "stream_universe": ("ReplacingMergeTree(v)", tbl(3)),
        "paper_state": ("ReplacingMergeTree(v)", tbl(2)),
        "ohlcv_1m": ("ReplacingMergeTree(version)", tbl(50)),
        "futures_ohlcv_1m": ("ReplacingMergeTree(version)", tbl(5)),
        "agent_runs": ("MergeTree", tbl(1)),
    }
    t.update(extra or {})
    return FakeCh(t)


def make(ch, store, tmp_path=None) -> StateSnapshot:
    return StateSnapshot(ch=ch, store=store, database="stocks", paper_dir=tmp_path)


def test_export_includes_everything_except_the_explicit_big_bar_tables():
    ch, store = source({"brand_new_table": ("MergeTree", tbl(1))}), MemStore()
    m = make(ch, store).export(now=T0)
    names = {t.name for t in m.tables}
    assert names == {"stream_universe", "paper_state", "agent_runs", "brand_new_table"}  # new table backed up automatically
    assert set(m.excluded) == {"ohlcv_1m", "futures_ohlcv_1m"} and set(m.excluded) <= EXCLUDED_TABLES
    assert m.complete and m.snapshot_id == "20261105T230000Z"


def test_latest_pointer_is_written_last_and_only_for_a_complete_snapshot():
    store = MemStore()
    ch = source()
    ch.fail_read.add("paper_state")
    m = make(ch, store).export(now=T0)
    assert not m.complete and "paper_state" in m.failed and "clickhouse exploded" in m.failed["paper_state"]
    assert "ch_state/latest.json" not in store.o  # a partial export must never become 'latest'
    assert {t.name for t in m.tables} >= {"stream_universe", "agent_runs"}  # the others still exported
    ch.fail_read.clear()
    m2 = make(ch, store).export(now=T0.replace(hour=23, minute=5))
    assert m2.complete and make(ch, store).latest_id() == m2.snapshot_id


def test_upload_that_does_not_match_its_size_is_a_failure():
    store = MemStore()
    store.truncate_puts = True
    m = make(source(), store).export(now=T0)
    assert not m.complete and all("stored size" in e for e in m.failed.values())
    assert "ch_state/latest.json" not in store.o


def test_round_trip_restores_rows_and_recreates_tables_from_the_manifest_ddl():
    store = MemStore()
    make(source(), store).export(now=T0)
    fresh = FakeCh({}, db="stocks")
    r = make(fresh, store).restore()
    assert r.ok
    assert {t.name: t.status for t in r.tables} == {n: "restored" for n in ("agent_runs", "paper_state", "stream_universe")}
    assert fresh.dbs["stocks"]["stream_universe"].num_rows == 3
    assert any(c.startswith("CREATE TABLE IF NOT EXISTS stocks.paper_state") for c in fresh.commands)


def test_restore_never_mixes_into_a_non_empty_table_unless_replace_is_explicit():
    store = MemStore()
    make(source(), store).export(now=T0)
    target = FakeCh({"stream_universe": ("ReplacingMergeTree(v)", tbl(9))})
    r = make(target, store).restore(tables=["stream_universe"])
    assert r.tables[0].status == "skipped_nonempty" and target.dbs["stocks"]["stream_universe"].num_rows == 9
    r2 = make(target, store).restore(tables=["stream_universe"], mode="replace")
    assert r2.tables[0].status == "restored" and target.dbs["stocks"]["stream_universe"].num_rows == 3


def test_corrupted_object_is_detected_by_checksum_not_inserted():
    store = MemStore()
    m = make(source(), store).export(now=T0)
    key = next(t.key for t in m.tables if t.name == "paper_state")
    store.o[key] = store.o[key][:-3] + b"xyz"
    fresh = FakeCh({})
    r = make(fresh, store).restore()
    bad = {t.name: t for t in r.tables}["paper_state"]
    assert bad.status == "error" and "checksum" in bad.error and not r.ok
    assert fresh.dbs["stocks"]["stream_universe"].num_rows == 3  # the others still restored


def test_restore_into_another_database_rewrites_the_ddl():
    store = MemStore()
    make(source(), store).export(now=T0)
    scratch = FakeCh({})
    make(scratch, store).restore(target_database="stocks_restore_test", tables=["agent_runs"])
    assert "CREATE DATABASE IF NOT EXISTS stocks_restore_test" in scratch.commands
    assert scratch.dbs["stocks_restore_test"]["agent_runs"].num_rows == 1


def test_paper_files_round_trip_and_existing_files_are_not_overwritten(tmp_path):
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "momentum_top50.json").write_text('{"a": 1}')
    (paper / "momentum_top50_alerts.jsonl").write_text('{"d": "2026-11-05"}\n')
    (paper / "notes.txt").write_text("ignored")
    store = MemStore()
    m = make(source(), store, paper).export(now=T0)
    assert {f.name for f in m.files} == {"momentum_top50.json", "momentum_top50_alerts.jsonl"}
    other = tmp_path / "restored"
    r = make(FakeCh({}), store, other).restore(restore_files=True)
    assert r.files_restored == 2 and (other / "momentum_top50_alerts.jsonl").read_text() == '{"d": "2026-11-05"}\n'
    (other / "momentum_top50.json").write_text("NEWER LOCAL")
    r2 = make(FakeCh({}), store, other).restore(restore_files=True)
    assert r2.files_skipped == 2 and (other / "momentum_top50.json").read_text() == "NEWER LOCAL"


def test_prune_keeps_the_newest_n_and_never_the_latest_pointer_target():
    store = MemStore()
    ch = source()
    for minute in range(5):
        make(ch, store).export(now=T0.replace(minute=minute))
    removed = make(ch, store).prune(keep=2)
    assert removed == ["20261105T230000Z", "20261105T230100Z", "20261105T230200Z"]
    assert {k.split("/")[1] for k in store.o if k.count("/") >= 2} == {"20261105T230300Z", "20261105T230400Z"}
    assert make(ch, store).latest_id() == "20261105T230400Z"


def test_restore_with_no_snapshot_fails_loudly():
    with pytest.raises(LookupError):
        make(FakeCh({}), MemStore()).restore()


def test_manifest_records_per_symbol_coverage_of_the_excluded_bar_tables():
    m = make(source(), MemStore()).export(now=T0)
    assert set(m.coverage) == {"ohlcv_1m", "futures_ohlcv_1m"}
    spy = {c.symbol: c for c in m.coverage["ohlcv_1m"]}["SPY"]
    assert spy.first == datetime(2006, 1, 3, tzinfo=timezone.utc) and spy.rows == 5000


def test_a_coverage_failure_makes_the_snapshot_incomplete():
    ch = source()
    ch.fail_read.add("ohlcv_1m")
    store = MemStore()
    m = make(ch, store).export(now=T0)
    assert not m.complete and "coverage:ohlcv_1m" in m.failed
    assert "ch_state/latest.json" not in store.o


def big(n_rows: int) -> FakeCh:
    return FakeCh({"stream_universe": ("ReplacingMergeTree(v)", tbl(n_rows)), "ohlcv_1m": ("ReplacingMergeTree(version)", tbl(1)),
                   "futures_ohlcv_1m": ("ReplacingMergeTree(version)", tbl(1))})


def test_a_snapshot_that_shrank_sharply_is_not_published_as_latest():
    store = MemStore()
    good = make(big(1000), store).export(now=T0)
    assert good.complete
    # e.g. the half-built box: its stream_universe has 14 rows, not 1000.
    bad = make(big(14), store).export(now=T0.replace(minute=5))
    assert not bad.complete and "shrink:stream_universe" in bad.failed
    assert make(big(14), store).latest_id() == good.snapshot_id  # latest did NOT move


def test_force_publishes_a_deliberately_smaller_snapshot():
    store = MemStore()
    make(big(1000), store).export(now=T0)
    forced = make(big(14), store).export(now=T0.replace(minute=5), force=True)
    assert forced.complete and make(big(14), store).latest_id() == forced.snapshot_id


def test_small_tables_and_normal_variation_do_not_trip_the_shrink_guard():
    store = MemStore()
    make(big(1000), store).export(now=T0)
    ok = make(big(900), store).export(now=T0.replace(minute=5))   # -10%: fine
    assert ok.complete
    tiny = MemStore()
    make(big(50), tiny).export(now=T0)                             # under SHRINK_MIN_ROWS
    assert make(big(1), tiny).export(now=T0.replace(minute=5)).complete


class TtlCh(FakeCh):
    """A table with a TTL: after the insert it expires some rows, like ingestion_runs."""

    def create_ddl(self, database, table):
        return f"CREATE TABLE {database}.{table} (`x` Int64) ENGINE = MergeTree TTL x + INTERVAL 90 DAY"

    def insert(self, database, table, data):
        super().insert(database, table, data.slice(0, max(1, data.num_rows - 3)))  # 3 rows expire immediately


def test_a_ttl_table_may_hold_fewer_rows_after_restore_but_not_a_non_ttl_one():
    store = MemStore()
    make(TtlCh({"runs": ("MergeTree", tbl(10))}), store).export(now=T0)
    ok = make(TtlCh({}), store).restore()
    assert ok.ok and ok.tables[0].status == "restored"

    class ShortCh(FakeCh):
        def insert(self, database, table, data):
            super().insert(database, table, data.slice(0, data.num_rows - 3))

    store2 = MemStore()
    make(FakeCh({"agent_runs": ("MergeTree", tbl(10))}), store2).export(now=T0)
    bad = make(ShortCh({}), store2).restore()
    assert not bad.ok and "expected 10" in bad.tables[0].error  # no TTL in its DDL: fewer rows is a failure
