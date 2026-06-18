# Coding Standards — StockAlert

Every silent failure has cost hours. Loud failures cost minutes. These
rules are load-bearing.

## Rules

### 1. `set -o pipefail` in bash + python pipelines

`tee` / `awk` / `jq` mask python's exit code without it.

```bash
# ❌ WRONG — python's exit code lost if tee succeeds
poetry run python my_script.py 2>&1 | tee /tmp/out.log

# ✅ RIGHT
set -o pipefail
poetry run python my_script.py 2>&1 | tee /tmp/out.log

# ✅ ALSO RIGHT (script-scope safer)
bash -c 'set -o pipefail; poetry run python my_script.py 2>&1 | tee /tmp/out.log'
```

### 2. Log every outcome — including zero / empty

"Got 0" must be distinguishable from "didn't run". Log first, then act.

```python
logger.info("fetched rows=%d sym=%s date=%s", len(rows), sym, d)
if not rows:
    return SinkResult(status="skipped", reason="no_rows")
```

### 3. Per-iteration completion markers in long loops

```python
# ✅ Allows operators to grep `year_complete=2024` and know exactly
# how far the loop got before any crash.
for year in years:
    ...do work...
    logger.info("year_complete=%d running_total=%d", year, total)
```

### 4. Catch-and-summarize exits non-zero on failure

`status="fail"` + `return 0` is a lie.

```python
if any(r.status == "error" for r in results):
    sys.exit(1)
```

### 5. Verify mutations cross-side

After Iceberg / CH write: reload via a **new** catalog/client and
assert.

```python
sink.append(arrow_df)
fresh = load_catalog()
assert fresh.load_table(name).current_snapshot().snapshot_id != prev_id
```

### 6. No bare `except` / `except Exception: pass`

Always `logger.exception()`. Inline comment if swallowed intentionally.

### 7. Preflight any > 5-min job

Fail-fast checks (creds, tables, paths) at the top.
Example: the preflight block in `scripts/lake_import_athena.py`.

### 8. Result objects > raises for predictable failures

Sinks return `SinkResult(status, error, metadata)` for
`"ok" | "skipped" | "error"`. Exceptions only for catastrophic,
abort-the-batch problems. Log AND return.

### 9. Iceberg upserts go through `chunked_upsert`

```python
from app.services.iceberg_safe_upsert import chunked_upsert

result = chunked_upsert(table, arrow_table, log_label="silver.foo")
# result.rows_updated, result.rows_inserted, result.chunks_committed
```

Never `table.upsert(...)` directly — PyIceberg 0.11.1 SIGBUSes past
~3k predicate nodes on macOS arm64. Tests in
`tests/test_iceberg_safe_upsert.py` pin the contract.

## Debugging a "successful" run that did nothing

Suspect in order: rule 1 (pipe mask) → rule 2 (silent zero) →
rule 6 (swallowed).
