"""
Tests for `iceberg_safe_upsert.chunked_upsert` — the single chokepoint
that guards every iceberg upsert in the codebase against PyIceberg's
multi-column predicate-tree SIGBUS.

What's pinned here
==================
- Chunking math: a 1500-row payload at chunk_size=400 produces 4
  upsert calls (400, 400, 400, 300).
- No double-counting: aggregated result.rows_updated /
  rows_inserted matches the sum of per-chunk PyIceberg results.
- Empty input is a clean no-op (no PyIceberg call, sane result).
- Each chunk's Arrow slice respects the chunk_size bound — this is
  THE invariant that prevents the SIGBUS regression.
- Validation: chunk_size <= 0 raises ValueError.

If a future refactor un-chunks the path (e.g. "let's just call
upsert directly"), `test_payload_split_into_safe_chunks` breaks.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from app.services.iceberg_safe_upsert import (
    DEFAULT_UPSERT_CHUNK_ROWS,
    ChunkedUpsertResult,
    chunked_upsert,
)


def _fake_arrow(n: int) -> pa.Table:
    """Build a tiny n-row Arrow table that's a valid input shape
    (one integer column). We never actually write it — the table
    mock catches the calls."""
    return pa.table({"x": list(range(n))})


def _fake_table(rows_updated: int = 0, rows_inserted: int = 0):
    """Mock PyIceberg Table whose .upsert() returns a fixed result."""
    table = MagicMock()
    table.upsert.return_value = MagicMock(
        rows_updated=rows_updated, rows_inserted=rows_inserted,
    )
    return table


class TestEmptyAndSmallInputs:
    def test_empty_input_is_noop(self) -> None:
        """0-row input must NOT call PyIceberg (saves a wasted commit)."""
        table = _fake_table()
        result = chunked_upsert(table, _fake_arrow(0))
        assert table.upsert.call_count == 0
        assert result == ChunkedUpsertResult()

    def test_below_chunk_size_takes_one_call(self) -> None:
        """N rows where N < chunk_size → exactly one .upsert() call."""
        table = _fake_table(rows_inserted=50)
        result = chunked_upsert(table, _fake_arrow(50))
        assert table.upsert.call_count == 1
        assert result.chunks_committed == 1
        assert result.total_rows == 50
        assert result.rows_inserted == 50

    def test_exactly_chunk_size_takes_one_call(self) -> None:
        """N == chunk_size → exactly one call (no off-by-one)."""
        table = _fake_table()
        chunked_upsert(table, _fake_arrow(DEFAULT_UPSERT_CHUNK_ROWS))
        assert table.upsert.call_count == 1


class TestChunkingMath:
    def test_payload_split_into_safe_chunks(self) -> None:
        """1500 rows / 400 per chunk → 4 chunks (400, 400, 400, 300).

        This is the load-bearing regression test — if anyone un-chunks
        the path, this fails immediately.
        """
        table = _fake_table()
        chunked_upsert(table, _fake_arrow(1500))

        assert table.upsert.call_count == 4

        # Every chunk's Arrow input must be ≤ chunk_size rows.
        chunk_sizes = [
            call.args[0].num_rows for call in table.upsert.call_args_list
        ]
        assert chunk_sizes == [400, 400, 400, 300]
        assert all(n <= DEFAULT_UPSERT_CHUNK_ROWS for n in chunk_sizes)

    def test_custom_chunk_size_respected(self) -> None:
        """Caller-supplied chunk_size overrides the default."""
        table = _fake_table()
        chunked_upsert(table, _fake_arrow(250), chunk_size=100)
        assert table.upsert.call_count == 3
        sizes = [c.args[0].num_rows for c in table.upsert.call_args_list]
        assert sizes == [100, 100, 50]

    def test_result_aggregates_per_chunk_counts(self) -> None:
        """rows_updated / rows_inserted accumulate across chunks."""
        # Build a table whose upsert returns rows_inserted=N for each
        # chunk to confirm summation.
        chunk_return = MagicMock(rows_updated=10, rows_inserted=30)
        table = MagicMock()
        table.upsert.return_value = chunk_return

        result = chunked_upsert(table, _fake_arrow(900), chunk_size=300)
        # 3 chunks × (10 updated + 30 inserted)
        assert result.chunks_committed == 3
        assert result.rows_updated == 30
        assert result.rows_inserted == 90
        assert result.total_rows == 900


class TestValidation:
    def test_zero_chunk_size_raises(self) -> None:
        table = _fake_table()
        with pytest.raises(ValueError, match="chunk_size must be > 0"):
            chunked_upsert(table, _fake_arrow(10), chunk_size=0)

    def test_negative_chunk_size_raises(self) -> None:
        table = _fake_table()
        with pytest.raises(ValueError, match="chunk_size must be > 0"):
            chunked_upsert(table, _fake_arrow(10), chunk_size=-1)


class TestExceptionsPropagate:
    def test_pyiceberg_errors_not_swallowed(self) -> None:
        """coding_standards.md rule 1F — we don't catch-and-swallow."""
        table = MagicMock()
        table.upsert.side_effect = RuntimeError("pyiceberg blew up")
        with pytest.raises(RuntimeError, match="pyiceberg blew up"):
            chunked_upsert(table, _fake_arrow(10))

    def test_mid_run_failure_committed_chunks_persist(self) -> None:
        """If chunk 3 of 5 raises, chunks 1+2 have already committed
        in PyIceberg. We re-raise so the caller's verify-mutation
        guard catches the partial state, but we don't silently lose
        what already landed."""
        n_calls = {"v": 0}

        def upsert_side_effect(arrow):
            n_calls["v"] += 1
            if n_calls["v"] == 3:
                raise RuntimeError("simulated crash on chunk 3")
            return MagicMock(rows_updated=0, rows_inserted=arrow.num_rows)

        table = MagicMock()
        table.upsert.side_effect = upsert_side_effect

        with pytest.raises(RuntimeError):
            chunked_upsert(table, _fake_arrow(1500), chunk_size=400)
        # Chunks 1 and 2 were attempted before chunk 3 raised.
        assert n_calls["v"] == 3
