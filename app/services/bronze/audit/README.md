# Bronze audit framework

The bronze layer is the foundation of every downstream consumer. If
bronze is wrong, every backtest, chart, screener, MCP tool, and
agent decision is wrong. This package is the structured audit that
catches bronze-level corruption, schema drift, or quality regressions
before they leak into silver.

**Run:**

```bash
poetry run python scripts/audit_bronze.py
poetry run python scripts/audit_bronze.py --check adjustment_status
poetry run python scripts/audit_bronze.py --out-json audit.json
```

## What's checked today

| Check | What it verifies |
|---|---|
| [schema_match](schema.py) | On-disk Iceberg schema == declared `BRONZE_*_SCHEMA` (field count, IDs, names, types, required flags, identifier fields). Catches schema drift before it breaks downstream. |
| [row_counts](row_counts.py) | Total records + data files + date range per table. Detects catastrophic regressions (empty table) + gives an operator baseline. |
| [source_tags](source_tags.py) | Distinct `source` values per table match the documented expected set ([bronze README](../README.md)). Catches typos and undocumented ingest paths. |
| [null_symbols](null_symbols.py) | Zero rows with null / empty / whitespace-only symbol. The historical Phase 1 import had ~80k of these; this asserts they haven't snuck back in. |
| [adjustment_status](adjustment_status.py) | Bronze rows match the documented `BRONZE_*_ADJUSTMENT_STATUS` constant. Verifies the ingest pipeline + lake state — the probe in `silver/probes/` tests the provider's API, this tests our actual data. |

## How it works (the pluggable pattern)

Same shape as the silver provider-adjustment probe framework:

```
BronzeAuditCheck (Protocol)
        │
        │  @register_check("name")
        ▼
   _CHECK_REGISTRY: dict[str, type]
        │
        │  build_all_checks()
        ▼
list[BronzeAuditCheck]
        │
        │  scripts/audit_bronze.py iterates: for each check × each table
        ▼
list[AuditResult]
        │
        ▼
 Terminal table + JSON report
```

## How to add a new check

Five steps:

1. **Create `<check>.py` in this directory.** Use `null_symbols.py`
   as the smallest template; `adjustment_status.py` as the deepest.

2. **Implement the `BronzeAuditCheck` Protocol** (`base.py`):

    ```python
    @register_check("my_new_check")
    class MyNewCheck:
        check_name = "my_new_check"

        def run(self, table_name: str) -> list[AuditResult]:
            # 1. safe_load_table(table_name) → (table, error)
            # 2. Scan the table (READ-ONLY; bounded)
            # 3. Return one or more AuditResult rows
            # NEVER raise — wrap exceptions as AuditStatus.SKIPPED
            ...
    ```

3. **Import it from `__init__.py`** so the decorator fires.

4. **Run** `poetry run python scripts/audit_bronze.py` to see it
   appear in the registry.

5. **Document** the check by adding a row to the "What's checked"
   table above.

## Rules every check MUST follow

These are enforced by the Protocol but listed here for clarity:

- **Read-only.** No mutations. Audits are observational.
- **Bounded.** No full-table scans without a column filter or
  partition prune. Bronze tables are GB-scale.
- **Never raise.** Wrap all exceptions into `AuditResult(status=SKIPPED, error=...)`.
- **Tolerate missing/empty tables.** `safe_load_table()` handles
  the missing case; checks should also handle the empty case.
- **Structured `details` dict.** Anything operationally relevant
  goes here (counts, samples, ratios) so the JSON report is
  machine-readable.
- **Severity grading.** Use `AuditSeverity.FAIL` only for "this is
  broken — fix now"; `WARN` for "suspicious — investigate";
  `INFO` for context.

## When to run

- **Before any silver build.** Bad bronze → bad silver.
- **In CI on every PR that touches `app/services/bronze/`** or
  `app/services/ingest/`.
- **Nightly via cron** with `--out-json` piped to a monitoring
  channel. Failing audits page; passing audits log silently.
- **On-demand** after any operator-level operation (Athena DELETE,
  schema migration, bulk backfill).
