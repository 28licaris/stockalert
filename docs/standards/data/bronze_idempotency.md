# Bronze Idempotency — Append-Only Hot Path

Bronze sinks **always `append`**. Never `overwrite(filter=...)` or
`delete(...)` in the hot path.

## Why

PyIceberg `overwrite` / `delete` read existing files to find affected
rows. On `bronze.polygon_minute` (35+ GB), that's hundreds of MB of
S3 I/O per write. Phase 1 dev: the call hung indefinitely.

## Model

- **Bronze:** `table.append(arrow_df)`. New file + snapshot. No read
  pass.
- **Idempotency upstream:** watermark ledger
  (`lake_archive_watermarks`) + ET-basis gap detection
  (`missing_weekdays`) for nightly; last-flushed-ts cursor for future
  live writer.
- **Silver-build dedups:** provider precedence + `argMax` per
  `(symbol, ts)`. Duplicate bronze rows are tolerated by design.

## Apply

New bronze sink → follow `app/services/bronze/sink.py`.
`BronzeIcebergSink.append(arrow)` is the entire write op.

## Maintenance → Athena, not PyIceberg

Cleanup / OPTIMIZE: Athena's partition-pruned
`DELETE … WHERE timestamp BETWEEN …` and
`OPTIMIZE … REWRITE DATA USING BIN_PACK`. Pattern:
`scripts/compact_bronze_monthly.py`. SQL quoting:
[`athena_dialects.md`](athena_dialects.md).
