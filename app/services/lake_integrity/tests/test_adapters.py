"""Pure parts of the adapters: Arrow conversion and the ClickHouse row mapping."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pyarrow as pa
import pytest

from app.services.lake_integrity.adapters import ClickHouseBarSource, bars_to_arrow
from app.services.lake_integrity.schemas import EQUITIES, FUTURES, Bar

NOW = datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc)
T = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)

BASE = [
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("open", pa.float64()), pa.field("high", pa.float64()), pa.field("low", pa.float64()),
    pa.field("close", pa.float64()), pa.field("volume", pa.float64()), pa.field("vwap", pa.float64()),
    pa.field("trade_count", pa.int64()), pa.field("source", pa.string()),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC")), pa.field("ingestion_run_id", pa.string()),
]
EQ_SCHEMA = pa.schema(BASE + [pa.field("adj_factor", pa.float64(), nullable=False)])
FUT_SCHEMA = pa.schema(BASE)


def _bar(**kw) -> Bar:
    d = dict(symbol="AAPL", timestamp=T, open=1, high=2, low=0.5, close=1.5, volume=9, vwap=1.2, trade_count=7, source="schwab-stream")
    d.update(kw)
    return Bar(**d)


def test_equities_rows_carry_adj_factor_one_and_provenance():
    t = bars_to_arrow(EQUITIES, [_bar(), _bar(symbol="MSFT")], "ch-archive-x", EQ_SCHEMA, NOW)
    assert t.num_rows == 2 and t.schema.equals(EQ_SCHEMA)
    assert t.column("adj_factor").to_pylist() == [1.0, 1.0]
    assert t.column("source").to_pylist() == ["schwab-stream"] * 2
    assert t.column("ingestion_run_id").to_pylist() == ["ch-archive-x"] * 2
    assert t.column("ingestion_ts").to_pylist() == [NOW, NOW]


def test_futures_rows_have_no_adj_factor():
    t = bars_to_arrow(FUTURES, [_bar(symbol="/ES")], "r", FUT_SCHEMA, NOW)
    assert "adj_factor" not in t.schema.names


def test_absent_vwap_and_trade_count_stay_null():
    t = bars_to_arrow(EQUITIES, [_bar(vwap=None, trade_count=None)], "r", EQ_SCHEMA, NOW)
    assert t.column("vwap").to_pylist() == [None] and t.column("trade_count").to_pylist() == [None]


def test_unknown_schema_column_fails_loudly():
    odd = pa.schema(list(EQ_SCHEMA) + [pa.field("mystery", pa.string())])
    with pytest.raises(KeyError):
        bars_to_arrow(EQUITIES, [_bar()], "r", odd, NOW)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows
        self.result_rows = [(r["d"],) for r in rows] if rows and "d" in rows[0] else []

    def named_results(self):
        return iter(self.rows)


class FakeClient:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def query(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        return FakeResult(self.rows)


def test_clickhouse_zero_means_absent_and_naive_times_become_utc():
    row = dict(symbol="AAPL", timestamp=datetime(2026, 7, 1, 14, 30), open=1, high=2, low=0.5, close=1.5,
               volume=9, vwap=0.0, trade_count=0, source="schwab-stream")
    src = ClickHouseBarSource(FakeClient([row]))
    [b] = src.bars(EQUITIES, date(2026, 7, 1))
    assert b.vwap is None and b.trade_count is None
    assert b.timestamp == T and b.timestamp.tzinfo is not None


def test_clickhouse_queries_use_final_source_filter_and_utc_day_bounds():
    client = FakeClient([{"d": date(2026, 7, 1)}])
    src = ClickHouseBarSource(client)
    assert src.days(EQUITIES, date(2026, 7, 1), date(2026, 7, 2)) == [date(2026, 7, 1)]
    sql, params = client.calls[0]
    assert "FROM ohlcv_1m FINAL" in sql and "source IN" in sql
    assert params["sources"] == list(EQUITIES.sources)
    assert params["lo"] == datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert params["hi"] == datetime(2026, 7, 3, tzinfo=timezone.utc)


class FakeScan:
    def __init__(self, arrow):
        self.arrow = arrow

    def to_arrow(self):
        return self.arrow


class FakeTable:
    def __init__(self, rows):
        self.rows, self.scans = rows, []

    def refresh(self):
        pass

    def scan(self, row_filter, selected_fields):
        self.scans.append(row_filter)
        return FakeScan(pa.table({"symbol": [r[0] for r in self.rows],
                                  "timestamp": pa.array([r[1] for r in self.rows], type=pa.timestamp("us", tz="UTC"))}))


class FakeCatalog:
    def __init__(self, table):
        self.table = table

    def load_table(self, _id):
        return self.table


def test_index_scans_a_week_once_and_serves_neighbouring_days_from_cache():
    from app.services.lake_integrity.adapters import IcebergBarIndex

    rows = [("AAPL", datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)),
            ("AAPL", datetime(2026, 7, 2, 14, 30, tzinfo=timezone.utc))]
    table = FakeTable(rows)
    idx = IcebergBarIndex(FakeCatalog(table))
    assert idx.keys(EQUITIES, date(2026, 7, 1)) == {rows[0]}
    assert idx.keys(EQUITIES, date(2026, 7, 2)) == {rows[1]}
    assert len(table.scans) == 1  # same chunk: one scan
    idx.keys(EQUITIES, date(2026, 7, 2), fresh=True)
    assert len(table.scans) == 2  # fresh re-reads


def test_index_returns_empty_for_a_day_the_lake_lacks():
    from app.services.lake_integrity.adapters import IcebergBarIndex

    idx = IcebergBarIndex(FakeCatalog(FakeTable([])))
    assert idx.keys(EQUITIES, date(2026, 7, 1)) == set()


def test_uuid_columns_are_exported_as_strings_and_others_untouched():
    from app.services.lake_integrity.adapters import select_list

    sel = select_list([("run_id", "UUID"), ("parent", "Nullable(UUID)"), ("name", "String"), ("ts", "DateTime64(3, 'UTC')")])
    assert sel == "toString(`run_id`) AS `run_id`, toString(`parent`) AS `parent`, `name`, `ts`"
    assert select_list([]) == "*"


# ── coverage counts DISTINCT minutes, one symbol at a time ────────────


class CoverageClient:
    """Answers the two query shapes ``ClickHouseStateClient.coverage`` issues."""

    def __init__(self, spans, distinct):
        self.spans, self.distinct, self.calls = spans, distinct, []

    def query(self, sql, parameters=None):
        self.calls.append((sql, parameters))

        class R:
            pass

        r = R()
        if "uniqExact" in sql:
            r.result_rows = [(self.distinct[parameters["s"]],)]
        else:
            r.result_rows = self.spans
        return r


def test_coverage_reports_distinct_minutes_not_raw_row_count():
    from app.services.lake_integrity.adapters import ClickHouseStateClient

    spans = [("AAPL", datetime(2006, 1, 3, 9, 20), datetime(2026, 11, 5, 23, 59)),
             ("SPY", datetime(2006, 1, 3, 9, 20), datetime(2026, 11, 5, 23, 59))]
    client = CoverageClient(spans, {"AAPL": 3_467_008, "SPY": 3_896_354})

    rows = ClickHouseStateClient(client).coverage("stocks", "ohlcv_1m")

    assert [(r[0], r[3]) for r in rows] == [("AAPL", 3_467_008), ("SPY", 3_896_354)]
    assert all(r[1].tzinfo is not None and r[2].tzinfo is not None for r in rows)


def test_coverage_never_issues_one_big_distinct_group_by():
    """A single `GROUP BY symbol` with uniqExact exhausted the dev box's ClickHouse memory
    (measured 2026-11-06). It must be one bounded query per symbol."""
    from app.services.lake_integrity.adapters import ClickHouseStateClient

    spans = [("AAA", datetime(2024, 1, 2), datetime(2024, 1, 3)), ("BBB", datetime(2024, 1, 2), datetime(2024, 1, 3))]
    client = CoverageClient(spans, {"AAA": 5, "BBB": 6})
    ClickHouseStateClient(client).coverage("stocks", "ohlcv_1m")

    distinct_sql = [(sql, p) for sql, p in client.calls if "uniqExact" in sql]
    assert [p["s"] for _, p in distinct_sql] == ["AAA", "BBB"]
    assert all("WHERE symbol = {s:String}" in sql and "GROUP BY" not in sql for sql, _ in distinct_sql)
    assert len(client.calls) == 3  # one span query + one per symbol


def test_duplicate_heavy_source_no_longer_inflates_the_expected_row_count():
    """The incident: the laptop's snapshot said AAPL had 5.88M rows (duplicates), more than
    the 5.03M minutes in 20.8 years, so a complete rebuild failed verification."""
    from app.services.lake_integrity.bootstrap import verify_coverage
    from app.services.lake_integrity.schemas import CoverageRow

    first, last = datetime(2006, 1, 3, tzinfo=timezone.utc), datetime(2026, 11, 5, tzinfo=timezone.utc)
    expected_distinct = CoverageRow(symbol="AAPL", first=first, last=last, rows=3_467_000)
    rebuilt = CoverageRow(symbol="AAPL", first=first, last=last, rows=3_467_008)
    assert verify_coverage([expected_distinct], [rebuilt]) == []
    inflated = CoverageRow(symbol="AAPL", first=first, last=last, rows=5_877_148)
    assert [p.kind for p in verify_coverage([inflated], [rebuilt])] == ["too_few_rows"]  # the old false alarm
