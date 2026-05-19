# Bronze Idempotency — Append-Only Hot Path

Bronze sinks in this repo **always `append`**, never
`overwrite(filter=...)` or `delete(...)` in the hot path.

## Why

PyIceberg's `overwrite(filter)` and `delete(filter)` both require
reading existing data files to determine which rows are affected. On
`bronze.polygon_minute` (35+ GB, 2.1B rows), that's hundreds of MB of
S3 I/O per write — completely unacceptable for a daily nightly job, much
less a 5-minute live writer.

This was hit during Phase 1 development; the call hung indefinitely.

## The idempotency model instead

- **Bronze**: always `table.append(arrow_df)`. Fast — just writes a new
  file and commits a snapshot. No read pass.

- **Upstream provides idempotency:**
  - Nightly jobs → watermark ledger (CH `lake_archive_watermarks`) +
    ET-basis gap detection (`missing_weekdays`).
  - Future live writer → "last-flushed-ts" cursor.

- **Silver-build handles dedup:** provider precedence + `argMax`-style
  per-`(symbol, ts)` selection. Duplicate bronze rows are tolerated
  because silver is the canonical layer.

## How to apply

When writing a new bronze sink (alpaca, databento, options-day, etc.),
follow `app/services/bronze/sink.py`. `BronzeIcebergSink.append(arrow)`
is the entire write op.

Don't introduce overwrite or delete in bronze.

## For maintenance — use Athena

Cleanup and compaction use Athena. Athena's partition-pruned
`DELETE ... WHERE timestamp BETWEEN ...` and
`OPTIMIZE table REWRITE DATA USING BIN_PACK` skip the PyIceberg slow
path entirely.

`scripts/compact_bronze_monthly.py` is the canonical pattern.

See [`athena_dialects.md`](athena_dialects.md) for the DDL / DML quoting
rules when writing Athena SQL from Python.

## Related

- [`lean_silver.md`](lean_silver.md) — silver is the canonical
  deduped layer; bronze tolerates duplicates by design.
- [`../coding.md`](../coding.md) rule 5 — verify cross-side after any
  write.
- [`../platform_design.md`](../platform_design.md) principle 5 —
  medallion architecture.
