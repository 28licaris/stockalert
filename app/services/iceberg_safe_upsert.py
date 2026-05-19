"""
Safe wrapper around PyIceberg's `Table.upsert()` — single chokepoint
for the chunked-write fix.

Why this module exists
======================

PyIceberg 0.11.1's `upsert_util.create_match_filter()` builds an
``Or(And(EqualTo, EqualTo, …), …)`` predicate tree with **one leaf
per source row** for multi-column identifiers (i.e. tables whose
``identifier-field-ids`` reference 2+ columns). For our schemas:

============================  ==================  ==================
table                         identifier          predicate factor
============================  ==================  ==================
silver.ohlcv_1m               (symbol, ts)        3 nodes/row
silver.bar_quality            (symbol, date)      3 nodes/row
silver.corp_actions           (symbol, ex_date,   5 nodes/row
                               action_type)
bronze.polygon_corp_actions   (symbol, ex_date,   5 nodes/row
                               action_type)
============================  ==================  ==================

PyIceberg then calls ``bind()`` + ``expression_to_pyarrow()`` which
walk this tree **recursively**. On macOS arm64 + Python 3.13 +
PyArrow 24.0.0 the C++ expression compiler exhausts its stack
between ~3,000 and ~6,000 nodes. The OS surfaces it as
**SIGBUS (Bus error: 10)** rather than SIGSEGV due to Apple
runtime guard-page handling — masquerading as a clean process exit
when wrapped in a shell pipeline without ``pipefail``.

For a single-column identifier PyIceberg uses the cheap
``In(col, [vals])`` path (constant tree size). Multi-column hits
the slow path with no such optimization.

Latest PyIceberg on PyPI (2026-05-18) = 0.11.1 — no upstream fix.

What this module does
=====================

``chunked_upsert(table, arrow, *, chunk_size=400)`` slices the input
Arrow table into batches whose predicate-tree size stays well below
the platform threshold, commits each chunk independently, and
aggregates the result.

The default ``chunk_size=400`` leaves ~2,000 expression nodes for
5-node-per-row tables (the worst case in our schema set), comfortably
below the ~3,000-node danger zone with headroom for PyIceberg /
PyArrow version drift.

**Cross-chunk semantics:** each chunk is its own Iceberg commit.
All-or-nothing across chunks is NOT preserved. This is acceptable
because every caller in this codebase is idempotent via the same
identifier — a partial-failure re-run heals the missing chunks
cleanly via the upsert key.

Logging contract (docs/standards/coding.md rule 1B): we log per-chunk
progress + totals, never silently. Zero-row inputs return cleanly
WITHOUT calling ``.upsert()`` (the underlying PyIceberg call would
no-op anyway but skipping it saves a wasted snapshot bump).

Bisection + reproducer: ``scripts/repro_corp_actions_sigbus_2.py``.
Root cause writeup: ``docs/iceberg_performance_findings.md``.

Usage
=====

.. code-block:: python

    from app.services.iceberg_safe_upsert import chunked_upsert

    result = chunked_upsert(table, arrow_batch)
    # result.rows_updated, result.rows_inserted match Table.upsert()'s
    # native UpsertResult shape — drop-in replacement.

Always use this helper instead of calling ``Table.upsert()`` directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)


# Default chunk size — bisected empirically 2026-05-18 against
# PyIceberg 0.11.1 + PyArrow 24.0.0 on macOS arm64 / Python 3.13.
# See module docstring for the root-cause analysis.
DEFAULT_UPSERT_CHUNK_ROWS: int = 400


@dataclass
class ChunkedUpsertResult:
    """Aggregate result of a chunked upsert call.

    Field names match PyIceberg's ``UpsertResult`` (``rows_updated``,
    ``rows_inserted``) so callers can swap raw ``.upsert()`` for
    ``chunked_upsert()`` without changing downstream code.
    """

    rows_updated: int = 0
    rows_inserted: int = 0
    chunks_committed: int = 0
    total_rows: int = 0


def chunked_upsert(
    table: Any,
    arrow: pa.Table,
    *,
    chunk_size: int = DEFAULT_UPSERT_CHUNK_ROWS,
    log_label: str | None = None,
) -> ChunkedUpsertResult:
    """Run ``table.upsert(arrow)`` in safe-sized batches.

    Args:
        table: PyIceberg Table instance (must have ``.upsert(...)``).
        arrow: PyArrow Table to upsert. May be empty (returns empty
            result without calling PyIceberg).
        chunk_size: Max rows per ``.upsert()`` call. Default is
            ``DEFAULT_UPSERT_CHUNK_ROWS=400`` which is safe for our
            5-node-per-row worst-case identifier schema (3-column).
            Tables with 4+ identifier columns should drop this lower.
        log_label: Optional string prefix for log lines (e.g. table
            short name). When None, logs are unlabeled.

    Returns:
        ``ChunkedUpsertResult`` aggregating across all chunks.

    Raises:
        Whatever ``table.upsert()`` raises — we do NOT swallow PyIceberg
        exceptions. (Catch-and-summarize is the caller's job per
        docs/standards/coding.md rule 1F.)
    """
    label = f"[{log_label}] " if log_label else ""

    total_rows = int(arrow.num_rows) if arrow is not None else 0
    if total_rows == 0:
        # Coding standards rule 1B: log even the zero case so callers
        # can distinguish "ran with empty input" from "didn't run".
        logger.info(
            "chunked_upsert: %sno rows to upsert (skipped)", label,
        )
        return ChunkedUpsertResult()

    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")

    n_chunks = (total_rows + chunk_size - 1) // chunk_size
    out = ChunkedUpsertResult(total_rows=total_rows)

    for chunk_idx in range(n_chunks):
        start = chunk_idx * chunk_size
        end = min(start + chunk_size, total_rows)
        chunk_arrow = arrow.slice(start, end - start)

        result = table.upsert(chunk_arrow)
        out.rows_updated += int(result.rows_updated)
        out.rows_inserted += int(result.rows_inserted)
        out.chunks_committed += 1

        logger.info(
            "chunked_upsert: %schunk %d/%d committed "
            "(rows=%d updated=%d inserted=%d)",
            label, chunk_idx + 1, n_chunks,
            end - start, result.rows_updated, result.rows_inserted,
        )

    logger.info(
        "chunked_upsert: %sdone total_rows=%d chunks=%d "
        "rows_updated=%d rows_inserted=%d",
        label, total_rows, n_chunks,
        out.rows_updated, out.rows_inserted,
    )
    return out
