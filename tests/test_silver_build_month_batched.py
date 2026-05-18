"""
Tests for the month-batched silver build path (TA-5.1.11).

The big claim: ONE bronze scan per provider per month replaces N×M
per-slice scans. This test suite proves:
  - One scan per provider per month (not per slice)
  - Output is byte-identical to the per-slice path (modulo run_id/ingestion_ts)
  - Month boundaries handled correctly (mid-month start/end, multi-month windows)
  - Empty months handled gracefully
  - Per-day upserts (one per silver table per day) preserved
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pyarrow as pa
import pytest

from app.services.silver.ohlcv.build import SilverOhlcvBuild


# ─────────────────────────────────────────────────────────────────────
# Fakes — counts scan calls per table so we can assert on them
# ─────────────────────────────────────────────────────────────────────


class _CountingScan:
    """Iceberg-scan stand-in that returns a filtered Arrow + records the
    fact that scan() was called (for "did month-batched scan ONCE?" assertions).
    """

    def __init__(self, all_rows: list[dict], counter: list, label: str) -> None:
        self._all = all_rows
        self._counter = counter
        self._label = label

    def to_arrow(self) -> pa.Table:
        return self._inner_arrow

    def __init_with_filter__(self, filtered_rows: list[dict]) -> None:
        if not filtered_rows:
            schema = pa.schema([
                pa.field("symbol", pa.string()),
                pa.field("timestamp", pa.timestamp("us", tz="UTC")),
                pa.field("open", pa.float64()),
                pa.field("high", pa.float64()),
                pa.field("low", pa.float64()),
                pa.field("close", pa.float64()),
                pa.field("volume", pa.int64()),
                pa.field("vwap", pa.float64()),
                pa.field("trade_count", pa.int64()),
                pa.field("source", pa.string()),
            ])
            self._inner_arrow = pa.Table.from_pylist([], schema=schema)
        else:
            self._inner_arrow = pa.Table.from_pylist(filtered_rows)


class _CountingBronzeTable:
    """Bronze fake that filters by row_filter AND counts scans."""

    def __init__(self, rows: list[dict], counter: list, label: str) -> None:
        self._rows = rows
        self._counter = counter
        self._label = label

    def scan(self, *, row_filter=None, selected_fields=None, **_kw):
        # Record the scan for the assertion.
        self._counter.append(self._label)

        target_syms = None
        t_lo, t_hi = None, None

        def _col_name(term) -> str:
            return getattr(term, "name", None) or str(term)

        def _to_dt(v):
            if isinstance(v, datetime):
                return v
            try:
                return datetime.fromtimestamp(v / 1_000_000, tz=timezone.utc)
            except (TypeError, ValueError):
                return None

        def _literal_value(expr):
            # In() carries `literals` (list); other predicates `literal` (single).
            if hasattr(expr, "literal"):
                return expr.literal.value
            if hasattr(expr, "literals"):
                return [lit.value for lit in expr.literals]
            return None

        def _walk(expr):
            nonlocal target_syms, t_lo, t_hi
            tname = type(expr).__name__
            if tname == "And":
                _walk(expr.left)
                _walk(expr.right)
            elif tname == "EqualTo":
                if _col_name(expr.term) == "symbol":
                    target_syms = [expr.literal.value]
            elif tname == "In":
                if _col_name(expr.term) == "symbol":
                    target_syms = _literal_value(expr) or []
            elif tname == "GreaterThanOrEqual":
                if _col_name(expr.term) == "timestamp":
                    t_lo = _to_dt(expr.literal.value)
            elif tname == "LessThan":
                if _col_name(expr.term) == "timestamp":
                    t_hi = _to_dt(expr.literal.value)

        if row_filter is not None:
            _walk(row_filter)

        filtered = [
            r for r in self._rows
            if (target_syms is None or r["symbol"] in target_syms)
            and (t_lo is None or r["timestamp"] >= t_lo)
            and (t_hi is None or r["timestamp"] < t_hi)
        ]

        scan_obj = _CountingScan(self._rows, self._counter, self._label)
        scan_obj.__init_with_filter__(filtered)
        return scan_obj


class _FakeSilverTable:
    def __init__(self) -> None:
        self.upserts: list[pa.Table] = []

    def upsert(self, arrow: pa.Table) -> None:
        self.upserts.append(arrow)


class _FakeCatalog:
    def __init__(self, bronze: dict[str, _CountingBronzeTable]) -> None:
        self._bronze = bronze

    def load_table(self, identifier: Any):
        short = (
            identifier[-1] if isinstance(identifier, tuple)
            else str(identifier).split(".")[-1]
        )
        if short in self._bronze:
            return self._bronze[short]
        from pyiceberg.exceptions import NoSuchTableError
        raise NoSuchTableError(short)


def _bronze_row(symbol: str, ts: datetime, *, close: float = 100.0,
                source: str = "polygon-flatfiles") -> dict:
    return {
        "symbol": symbol, "timestamp": ts,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "vwap": close, "trade_count": 5,
        "source": source,
    }


def _make_build(rows_per_symbol_day: dict[tuple[str, date], int]):
    """Build a SilverOhlcvBuild + per-table scan counter."""
    all_rows: list[dict] = []
    for (sym, day), n in rows_per_symbol_day.items():
        for m in range(n):
            ts = datetime(
                day.year, day.month, day.day, 14, 30 + m, tzinfo=timezone.utc,
            )
            all_rows.append(_bronze_row(sym, ts, close=100.0 + m * 0.01))

    counter: list[str] = []
    bronze = {
        "polygon_minute": _CountingBronzeTable(all_rows, counter, "polygon_minute"),
    }
    catalog = _FakeCatalog(bronze)
    ohlcv_table = _FakeSilverTable()
    bq_table = _FakeSilverTable()
    build = SilverOhlcvBuild(
        catalog=catalog,
        ohlcv_table=ohlcv_table,
        bar_quality_table=bq_table,
        provider_precedence=["polygon"],
    )
    # Pre-prime corp-actions cache.
    build._split_index = {}
    build._corp_actions_arrow = pa.table({"symbol": []})
    return build, ohlcv_table, bq_table, counter


# ─────────────────────────────────────────────────────────────────────
# _iter_months
# ─────────────────────────────────────────────────────────────────────


class TestIterMonths:
    def test_single_month(self) -> None:
        build = SilverOhlcvBuild(
            catalog=object(), ohlcv_table=object(),
            bar_quality_table=object(), provider_precedence=["polygon"],
        )
        months = list(build._iter_months(
            date(2024, 6, 5), date(2024, 6, 20),
        ))
        assert months == [(date(2024, 6, 1), date(2024, 6, 30))]

    def test_multi_month_spans_calendar_boundary(self) -> None:
        build = SilverOhlcvBuild(
            catalog=object(), ohlcv_table=object(),
            bar_quality_table=object(), provider_precedence=["polygon"],
        )
        months = list(build._iter_months(
            date(2024, 6, 15), date(2024, 8, 10),
        ))
        assert months == [
            (date(2024, 6, 1), date(2024, 6, 30)),
            (date(2024, 7, 1), date(2024, 7, 31)),
            (date(2024, 8, 1), date(2024, 8, 31)),
        ]

    def test_december_to_january_year_boundary(self) -> None:
        build = SilverOhlcvBuild(
            catalog=object(), ohlcv_table=object(),
            bar_quality_table=object(), provider_precedence=["polygon"],
        )
        months = list(build._iter_months(
            date(2023, 12, 15), date(2024, 1, 10),
        ))
        assert months == [
            (date(2023, 12, 1), date(2023, 12, 31)),
            (date(2024, 1, 1), date(2024, 1, 31)),
        ]


# ─────────────────────────────────────────────────────────────────────
# Scan-count assertions: the headline claim
# ─────────────────────────────────────────────────────────────────────


class TestScanCount:
    def test_one_scan_per_provider_per_month(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The headline claim. 5 symbols × 60 days (spanning 2 months)
        = 300 slices under the per-slice path = 300 scans.
        Month-batched should be EXACTLY 2 (one per month).
        """
        # 5 symbols × 60 days = 300 slices total, spanning June + July.
        d0 = date(2024, 6, 1)
        d_end = date(2024, 7, 30)
        from datetime import timedelta
        symbols = ["AAPL", "NVDA", "MSFT", "GOOGL", "META"]
        rows_map: dict[tuple[str, date], int] = {}
        cur = d0
        while cur <= d_end:
            for s in symbols:
                rows_map[(s, cur)] = 5
            cur += timedelta(days=1)

        build, _, _, counter = _make_build(rows_map)
        monkeypatch.setattr(build, "_record_run", lambda _r: None)

        # Default mode=month.
        build.build_window(symbols, d0, d_end)

        # ONE polygon_minute scan per month × 2 months = 2 scans total.
        assert counter == ["polygon_minute", "polygon_minute"]

    def test_per_slice_makes_one_scan_per_slice(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sanity counter-check: per-slice mode makes ~symbols × days
        scans, demonstrating why the month-batched path is so much
        cheaper."""
        symbols = ["AAPL", "NVDA"]
        d0 = date(2024, 6, 10)
        d1 = date(2024, 6, 11)
        rows_map = {(s, d): 5 for s in symbols for d in (d0, d1)}
        build, _, _, counter = _make_build(rows_map)
        monkeypatch.setattr(build, "_record_run", lambda _r: None)

        build.build_window(
            symbols, d0, d1, mode="per-slice", max_concurrency=1,
        )

        # 2 symbols × 2 days × 1 provider = 4 scans.
        assert counter.count("polygon_minute") == 4


# ─────────────────────────────────────────────────────────────────────
# Output equivalence with per-slice path
# ─────────────────────────────────────────────────────────────────────


class TestOutputEquivalence:
    """Month-batched output must be byte-identical to per-slice
    (modulo ingestion_ts/run_id which we strip before comparing)."""

    def _normalize_arrow_for_compare(self, t: pa.Table) -> list[dict]:
        """Drop ingestion_ts / ingestion_run_id; sort by (symbol, ts)."""
        keep = [c for c in t.column_names
                if c not in ("ingestion_ts", "ingestion_run_id")]
        rows = t.select(keep).to_pylist()
        rows.sort(key=lambda r: (r.get("symbol", ""), r.get("timestamp")))
        return rows

    def test_month_batched_matches_per_slice(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        symbols = ["AAPL", "NVDA", "MSFT"]
        d0 = date(2024, 6, 10)
        d1 = date(2024, 6, 12)
        rows_map = {
            (s, d): 5
            for s in symbols
            for d in (d0, date(2024, 6, 11), d1)
        }

        # Per-slice run.
        b1, ohlcv1, bq1, _ = _make_build(rows_map)
        monkeypatch.setattr(b1, "_record_run", lambda _r: None)
        b1.build_window(
            symbols, d0, d1, mode="per-slice", max_concurrency=1,
        )

        # Month-batched run.
        b2, ohlcv2, bq2, _ = _make_build(rows_map)
        monkeypatch.setattr(b2, "_record_run", lambda _r: None)
        b2.build_window(symbols, d0, d1)  # default = month

        # Combine each path's upserts into one Arrow for comparison.
        def _concat(uppers):
            non_empty = [u for u in uppers if u.num_rows > 0]
            return (
                pa.concat_tables(non_empty)
                if non_empty else pa.table({})
            )

        per_slice_rows = self._normalize_arrow_for_compare(_concat(ohlcv1.upserts))
        month_rows = self._normalize_arrow_for_compare(_concat(ohlcv2.upserts))

        assert per_slice_rows == month_rows
        assert (
            len(_concat(ohlcv1.upserts)) == len(_concat(ohlcv2.upserts))
        )


# ─────────────────────────────────────────────────────────────────────
# Per-day upserts within a month
# ─────────────────────────────────────────────────────────────────────


class TestPerDayUpserts:
    def test_one_upsert_per_table_per_day(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Within a month, each day still produces ONE upsert per
        silver table — same commit-conflict mitigation as the
        concurrent path."""
        from datetime import timedelta

        symbols = ["AAPL", "NVDA"]
        d0 = date(2024, 6, 10)
        d_end = date(2024, 6, 14)  # 5 trading days
        rows_map = {
            (s, d0 + timedelta(days=i)): 5
            for s in symbols
            for i in range(5)
        }
        build, ohlcv_t, bq_t, _ = _make_build(rows_map)
        monkeypatch.setattr(build, "_record_run", lambda _r: None)

        build.build_window(symbols, d0, d_end)

        # 5 days → 5 ohlcv upserts + 5 bar_quality upserts.
        assert len(ohlcv_t.upserts) == 5
        assert len(bq_t.upserts) == 5
        # Each upsert has BOTH symbols' data for that day.
        for u in ohlcv_t.upserts:
            assert u.num_rows == 2 * 5   # 2 symbols × 5 bars


# ─────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_month_processes_zero_writes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A month with no bronze rows → no upserts. The slice results
        still record one entry per (symbol, day) for accounting."""
        build, ohlcv_t, bq_t, _ = _make_build({})  # zero rows
        monkeypatch.setattr(build, "_record_run", lambda _r: None)

        result = build.build_window(
            ["AAPL", "NVDA"], date(2024, 6, 10), date(2024, 6, 11),
        )
        # 2 symbols × 2 days = 4 slice results.
        assert len(result.slices) == 4
        assert all(s.succeeded for s in result.slices)
        assert ohlcv_t.upserts == []
        assert bq_t.upserts == []

    def test_partial_month_at_window_start(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Window starting mid-month: only days from start_date onward
        are processed, but the scan still pulls the WHOLE month
        (Iceberg partition-prune is by month, so we get whatever
        month we ask for)."""
        from datetime import timedelta

        symbols = ["AAPL"]
        # Bronze rows exist for the entire month of June.
        rows_map = {("AAPL", date(2024, 6, d)): 5 for d in range(1, 16)}
        build, _, _, _ = _make_build(rows_map)
        monkeypatch.setattr(build, "_record_run", lambda _r: None)

        # But the operator only wants June 10-15.
        result = build.build_window(
            symbols, date(2024, 6, 10), date(2024, 6, 15),
        )

        # 6 days × 1 symbol = 6 slice results (10, 11, 12, 13, 14, 15).
        assert len(result.slices) == 6
        slice_dates = sorted({s.date for s in result.slices})
        assert slice_dates == [
            date(2024, 6, d) for d in range(10, 16)
        ]


# ─────────────────────────────────────────────────────────────────────
# Invalid mode
# ─────────────────────────────────────────────────────────────────────


class TestInvalidMode:
    def test_unknown_mode_raises(self) -> None:
        build, _, _, _ = _make_build({})
        with pytest.raises(ValueError, match="unknown mode"):
            build.build_window(
                ["AAPL"], date(2024, 6, 10), date(2024, 6, 10),
                mode="bogus",
            )
