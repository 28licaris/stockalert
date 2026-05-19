# Coding — NO SILENT FAILURES

The prime directive. Every silent failure in this repo's history has
cost hours. Loud failures cost minutes. The asymmetry is enormous;
treat these rules as load-bearing.

## Hard rules (non-negotiable)

### 1. Bash and python pipelines need `set -o pipefail`

Without it, `tee` / `awk` / `jq` mask python's exit code. We lost hours
to corp-actions backfills "succeeding" while python was SIGKILLed.

```bash
set -euo pipefail
poetry run python scripts/x.py | tee logs/x.log
```

### 2. Log all outcomes, including zero / empty / no-op

`if rows:` skipping the log line makes "got 0" indistinguishable from
"didn't run". Always log first, then act.

```python
logger.info("fetched rows=%d for symbol=%s date=%s", len(rows), sym, d)
if not rows:
    return SinkResult(status="skipped", reason="no_rows")
```

### 3. Per-iteration completion markers in long loops

Operators must be able to grep how far the loop got before any crash.

```python
for year in range(start, end + 1):
    process_year(year)
    logger.info("year_complete=%d", year)
```

### 4. Catch-and-summarize must exit non-zero on failure

Setting `status="fail"` then `return 0` (or `exit 0`) is a
worse-than-useless lie — the caller treats it as success.

```python
if any(r.status == "error" for r in results):
    sys.exit(1)
```

### 5. Verify mutations cross-side

After Iceberg / ClickHouse writes, load the table via a **new** catalog
or client instance and assert the snapshot ID changed (or rows are
present). "No exception raised" ≠ "data was written".

```python
sink.append(arrow_df)
fresh_catalog = load_catalog()  # new instance
table = fresh_catalog.load_table(name)
assert table.current_snapshot().snapshot_id != prev_snapshot_id
```

### 6. No bare `except:` or `except Exception: pass`

Always `logger.exception()` minimum. If swallowing is intentional,
explain why in a comment on the same line.

```python
try:
    cleanup_temp_file(path)
except OSError:
    logger.exception("cleanup failed for path=%s", path)
    # swallowed: temp dir cleanup is best-effort, run continues
```

### 7. Preflight checks for any > 5-minute job

Every post-mortem produces a new preflight check — never the same
silent failure twice. Preflights live at the top of long-running
scripts and fail fast on missing creds, missing tables, unreachable
endpoints.

Example: `scripts/preflight_silver_build.py` runs before the silver
build.

### 8. Result objects > raises for predictable-failure paths

Sinks/clients return `SinkResult(status, error, metadata)` for expected
outcomes (`"ok"`, `"skipped"`, `"error"`). Reserve exceptions for
catastrophic, abort-the-whole-batch problems. This way one sink's
failure does not take down others in a fan-out.

Result objects do **not** replace logging. Log AND return.

### 9. Iceberg upserts go through `chunked_upsert`. Always.

```python
from app.services.iceberg_safe_upsert import chunked_upsert
chunked_upsert(table, arrow, log_label="silver.foo")
```

**Never call `table.upsert(...)` directly** in app/scripts code.
PyIceberg 0.11.1's multi-column upsert builds an O(N) predicate tree
that SIGBUSes PyArrow's C++ compiler past ~3,000 nodes on macOS arm64.
The helper chunks at 400 rows to stay safe.

Tests in `tests/test_iceberg_safe_upsert.py` pin the contract.

## When debugging a "successful" run that did nothing

Suspect in this order:

1. **Pipeline-mask bug** (rule 1). `pipefail` missing somewhere.
2. **Zero-row skipped log** (rule 2). The "got 0" path silently no-op'd.
3. **Catch-and-swallow** (rule 6). An exception was eaten.

## Behavior expectation before writing ingest / build / cron / script code

1. Read this doc, rules 1–4 minimum.
2. Identify which rules apply to the diff.
3. If about to violate one (or work around tool quirk a second time):
   stop. Surface the tradeoff. Fix the tool, not the symptom.

## Open audit list

Each silent failure encountered must produce an entry here until
neutralized:

- _(none currently — add a row with date + summary when a new class of
  silent failure is caught)_

## Related

- [`data/bronze_idempotency.md`](data/bronze_idempotency.md) — write
  idempotency.
- [`data/timezone_et_vs_utc.md`](data/timezone_et_vs_utc.md) — silent
  date-bucket misclassification.
- [`data/athena_dialects.md`](data/athena_dialects.md) — silent SQL
  parser surprise.
- [`service_modules.md`](service_modules.md) — result objects and
  factory patterns.
- [`engagement.md`](engagement.md) — spec-first signoff.
