# Coding Standards — StockAlert

> **Read first.** These rules are not advisory. Every PR, every script
> change, every helper added must obey them. They exist because we
> have repeatedly burned hours debugging issues that should never have
> shipped. Each rule was paid for in real time lost.

The standards are organized from "absolutely never break" to
"strongly preferred." If a piece of new or modified code conflicts
with a rule and you believe there's a real reason to deviate, surface
the tradeoff explicitly and get signoff first. Plan docs are guidance,
not pre-authorization (see
[feedback_lean_silver_explicit_signoff.md](../.. /.claude/projects/-Users-licaris-dev-stockalert/memory/feedback_lean_silver_explicit_signoff.md)).

---

## Rule 1 — NO SILENT FAILURES (the prime directive)

**Failures must be loud, visible, and traceable.** Every catch block,
every wrapper script, every CLI command must be auditable from the
outside without reading code.

### What "silent failure" means here

Examples that have actually shipped to this codebase and cost us
hours:

| Pattern | What we lost |
|---|---|
| `tee` in a pipe masking python's exit code | 6+ hrs debugging "successful" corp_actions backfills that never wrote |
| `if rows:` skipping the log line when 0 rows | Couldn't tell "got 0" from "didn't run" |
| `except Exception: pass` swallowing errors | Production bugs unobserved for weeks |
| Bare `cat result.json` with no schema check | Operator sees JSON, doesn't notice key missing |
| `print()` instead of `logger.exception()` | Stack traces lost when stdout buffer truncates |
| Script exits 0 but did nothing | Operator assumes success, moves to next step |

### The hard rules

**A) Bash pipelines that include Python MUST use `pipefail`.**

```bash
# ❌ WRONG — python's exit code lost if tee succeeds
poetry run python my_script.py 2>&1 | tee /tmp/out.log

# ✅ RIGHT
set -o pipefail
poetry run python my_script.py 2>&1 | tee /tmp/out.log

# ✅ ALSO RIGHT (script-scope safer)
bash -c 'set -o pipefail; poetry run python my_script.py 2>&1 | tee /tmp/out.log'
```

The same applies to `xargs`, `awk`, `jq` — anything downstream of a
critical command.

**B) Log ALL outcomes, including "0 rows / empty / no-op".**

```python
# ❌ WRONG — caller can't distinguish "ran, got 0" from "never ran"
chunk_splits = await client.collect_splits(...)
if chunk_splits:
    logger.info("pulled %d splits", len(chunk_splits))
    upsert(table, chunk_splits)

# ✅ RIGHT
chunk_splits = await client.collect_splits(...)
logger.info("pulled %d splits (year=%d)", len(chunk_splits), year)
if chunk_splits:
    upsert(table, chunk_splits)
```

**C) Long loops must emit a per-iteration completion marker.**

```python
# ✅ Allows operators to grep `year_complete=2024` and know exactly
# how far the loop got before any crash.
for year in years:
    ...do work...
    logger.info("year_complete=%d running_total=%d", year, total)
```

**D) Catch-and-summarize patterns MUST re-raise OR exit non-zero.**

```python
# ❌ WRONG — sets status=fail but script still returns 0
try:
    do_thing()
except Exception as e:
    summary["status"] = "fail"
    summary["error"] = str(e)
return 0   # ← BUG: caller thinks we succeeded

# ✅ RIGHT
try:
    do_thing()
except Exception as e:
    summary["status"] = "fail"
    summary["error"] = str(e)
    logger.exception("do_thing failed")
return 0 if summary["status"] == "ok" else 2
```

**E) Every script that mutates state MUST verify the mutation happened
before exiting "ok".**

For ingest jobs: read back the row count or snapshot ID and assert
the write took effect. Don't trust "no exception raised" as proof of
success — Iceberg `upsert` returns silently if the input is empty;
PyIceberg can be killed mid-commit and the table remains unchanged.

**F) NEVER use bare `except:` or `except Exception: pass`.**

Use `logger.exception()` minimum. If you must swallow, document
exactly why and what the recovery is.

**G) Result objects > raises for predictable-failure paths.**

Per [service module design](../.claude/projects/-Users-licaris-dev-stockalert/memory/feedback_service_module_design.md):
return `BuildResult(succeeded=False, error=...)` instead of raising.
But: result objects don't replace logging — log AND return.

---

## Rule 2 — Validation BEFORE long-running work

Anything that costs > 5 minutes of wall-clock must have a preflight
check. The check runs in seconds; the work runs in hours. The
asymmetry is what makes preflights pay for themselves the first time
they catch a bug.

Current preflights:
- `scripts/preflight_silver_build.py` — 7 checks before silver --full
- (add new ones here as they land)

**Preflight rule of thumb:** every silent-failure post-mortem produces
a new preflight check. If we lost time because "X was wrong but we
didn't notice," that exact check goes into the preflight script
permanently.

Example: TA-5.0 found bronze.corp_actions had truncated years (5,108
rows for 2024 instead of ~200K). The preflight now has
`check_corp_actions_year_coverage` that fails before silver --full
would silently produce wrong adjusted prices.

---

## Rule 3 — Verify mutations cross-side

Wrote to Iceberg? Read it back via a NEW catalog instance and confirm
the row count delta matches. Wrote to ClickHouse? Same — query the
target table and assert.

Cross-side verification catches: caching, stale handles, batch
serialization bugs, partial commits, async write reordering.

Concrete pattern in this codebase:

```python
# After ingest writes:
fresh_cat = get_catalog()  # new instance, no cache
fresh = fresh_cat.load_table(bronze_table_id("polygon_corp_actions"))
post_rows = int(
    fresh.current_snapshot().summary.additional_properties["total-records"]
)
assert post_rows > pre_rows, (
    f"ingest claimed success but row count unchanged "
    f"(pre={pre_rows} post={post_rows})"
)
```

---

## Rule 4 — Idempotency contracts are explicit

Bronze appends. Silver dedups. **Never** `overwrite(filter=...)` or
`delete(filter=...)` on the hot path — PyIceberg may read existing
files. (See
[feedback_bronze_idempotency_model.md](../.claude/projects/-Users-licaris-dev-stockalert/memory/feedback_bronze_idempotency_model.md).)

Every ingest method documents:
- What identifier makes a row unique (upsert key).
- What happens on re-run with same window (must be a no-op or a clean
  refresh — never duplication, never partial state).
- The maintenance recipe for cleanup (Athena, compaction job, etc.).

---

## Rule 5 — Time zones are EXPLICIT

US equities trading day = ET 04:00–20:00. After-hours bars cross
midnight UTC, so UTC-date misclassifies them. (See
[feedback_et_vs_utc_trading_day.md](../.claude/projects/-Users-licaris-dev-stockalert/memory/feedback_et_vs_utc_trading_day.md).)

Use `yesterday_et()` or `astimezone(NY).date()` — never bare
`datetime.now().date()` for trading-day arithmetic.

---

## Rule 6 — Docs travel with code

Every microservice folder has a `README.md`. Adding or changing a
service ALSO updates the relevant plan doc + the journal in the same
change. Drift breaks the "pick up where we left off" promise.

---

## Rule 7 — Tests pin contracts, not implementations

Every silent-failure bug we ship should produce a test that fails on
the regression. Examples already pinned:

- `test_silver_corp_actions.py::TestBackfillFullHistoryYearChunking`
  — locks in the OOM fix (must chunk per calendar year).
- `test_silver_corp_actions.py::TestDedupeActions::test_duplicate_cash_dividends_summed`
  — locks in the Polygon-same-day-dividend collapse.

---

## Rule 8 — When tooling fights you, fix the tooling

If a silent failure happens because of a quirk of bash, PyIceberg,
asyncio — don't memorize the quirk and tiptoe around it. Add a helper,
a wrapper, a test, or a doc rule that prevents anyone from stepping
on it again. The same tool quirk should NEVER cost us time twice.

---

## Rule 9 — Iceberg upserts go through `chunked_upsert`. Always.

**Never call `table.upsert(...)` directly in app/scripts code.**
Always import the centralized helper:

```python
from app.services.iceberg_safe_upsert import chunked_upsert

result = chunked_upsert(table, arrow_table, log_label="silver.foo")
# result.rows_updated, result.rows_inserted, result.chunks_committed
```

### Why this rule exists (paid for in real time)

PyIceberg 0.11.1's `upsert()` builds an O(N) predicate tree for
multi-column identifiers and PyArrow's C++ expression compiler
SIGBUSes on macOS arm64 past ~3,000 expression nodes (≈ 600-1,200
rows). We hit this twice — once on `bronze.polygon_corp_actions`
that cost hours of "successful" runs writing nothing. The
SIGBUS was masked by `tee` in a non-pipefail shell pipeline so
exit code was 0 (see rule 1A).

The fix centralizes the chunking in
[`app/services/iceberg_safe_upsert.py`](../app/services/iceberg_safe_upsert.py),
which slices the input Arrow table into batches of 400 rows and
commits each chunk independently. Same shape as `Table.upsert()` —
`rows_updated`, `rows_inserted` — so it's a drop-in replacement.

Full root-cause writeup in
[`docs/iceberg_performance_findings.md`](iceberg_performance_findings.md)
(see "Addendum 2026-05-18 — PyIceberg multi-column upsert SIGBUS").

### What this rule blocks

- Any PR adding `table.upsert(...)` in app or scripts code that
  doesn't go through the helper. Reviewers MUST reject.
- Any "let's just call upsert directly, the table's small" — the
  threshold depends on identifier-column count, not target-table
  size. Future you doesn't know what column counts the consumer
  uses.

### Tests that pin this

- [`tests/test_iceberg_safe_upsert.py`](../tests/test_iceberg_safe_upsert.py)
  — 10 tests on the helper itself (chunking math, empty inputs,
  validation, exception propagation).
- `tests/test_silver_corp_actions.py::test_upsert_routes_through_chunked_upsert`
  — pins that the corp_actions ingest delegates correctly.
- A new test should land per call site that wires the helper in.

---

## How this doc is used

1. **Auto-loaded.** A feedback memory file references this doc so
   agents pick it up as project context before starting work. See
   `feedback_coding_standards.md` in the memory folder.
2. **Audit-ready.** When a silent failure surfaces, the post-mortem
   slot goes here. Each new rule = one new bug class neutralized.
3. **Mandatory diff review.** PRs that touch ingest, silver build,
   any cron job, or any script must explicitly tag which rules apply
   and how the code satisfies them.

---

## Open audit list

Issues inspected for silent-failure patterns:

- [x] `scripts/run_corp_actions_backfill.py` — verify-mutation
      snapshot-ID assertion landed 2026-05-18. Bronze upsert that
      silently no-ops now raises `RuntimeError("bronze upsert NO-OP detected")`.
- [x] Every `.upsert()` call site routed through `chunked_upsert`
      helper (rule 9) to neutralize the PyIceberg SIGBUS — 2026-05-18.
- [x] `scripts/run_silver_ohlcv_build.py` — verify-mutation guard
      landed 2026-05-18 (`_capture_silver_state` +
      `_enforce_mutation_contract`). Reports pre/post snapshot IDs +
      row deltas; raises if build claimed rows but ohlcv_1m snapshot
      didn't advance.
- [x] All shell scripts in `scripts/` — audited 2026-05-18, every
      `.sh` file has `set -o pipefail`. No bash pipelines using `tee`
      in app/scripts code. The `tee` issue we hit was in ad-hoc
      Bash-tool commands (agent behavior, not codebase).
- [x] Schwab nightly + Polygon nightly — audited 2026-05-18. Both
      follow the same pattern: per-cycle errors logged via
      `logger.exception()`, loop continues. **This is correct for
      in-process cron loops** (one bad night shouldn't crash FastAPI).
      The real gap is **observability** — no heartbeat metric, no
      consecutive-failure alert. That's a separate concern (TA-OBS-x
      to be planned later) — not a silent-failure category.

**Audit complete as of 2026-05-18.** Next silent-failure post-mortem
adds a new entry here and the cycle repeats. Don't let it grow stale —
each "this would've been caught earlier if…" becomes a new rule + a
new test.
