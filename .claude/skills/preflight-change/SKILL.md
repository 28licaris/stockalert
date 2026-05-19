---
name: preflight-change
description: Run before committing any non-trivial code change. Walks the NO-SILENT-FAILURES standards checklist, runs the relevant tests, and (for ingest/build/script changes) verifies that any data mutation is checked cross-side per coding-standards rule 5. Trigger when the user says "preflight", "ready to commit", "verify before push", or any signoff phrase. Do NOT commit automatically — report and wait.
---

# Preflight a change

Run this before any commit that touches more than a trivial line. Output
is a structured report; the user decides whether to commit.

## Step 1 — Read what changed

```
git status
git diff --stat
git diff               # full diff for files in scope
```

Classify the change. Different categories get different checks:

- **ingest / build / cron / scripts** — full standards + mutation verify
- **app/services/** module code — standards + tests + contract check
- **app/api/** route — standards + tests + contract check
- **tests** — standards (logging, no bare except) + run the new tests
- **docs only** — skip standards; verify links resolve
- **config / pyproject** — surface to user before running anything

## Step 2 — Standards checklist

For each rule, state **PASS / N/A / FAIL** with a one-line reason.
Reference: [`docs/standards/coding.md`](../../../docs/standards/coding.md).

1. **Pipefail.** Any new bash pipeline has `set -o pipefail`?
2. **Log every outcome.** Including zero/empty/no-op? (Search the diff
   for `if rows:` / `if results:` patterns that skip a log line.)
3. **Loop progress markers.** Long loops emit per-iteration completion
   (`logger.info("year_complete=%d", year)`)?
4. **Catch-and-summarize exits non-zero on fail.** No `status="fail"`
   followed by `return 0`?
5. **Cross-side mutation verify.** After Iceberg/CH write, the code
   loads via a *new* catalog/client and asserts on snapshot ID or row
   count? (The verify must be in the diff — not just performed by hand.)
6. **No bare `except:` or `except Exception: pass`.** Every swallow has
   `logger.exception()` and an inline comment explaining why?
7. **Preflight checks for >5-min jobs.** Any new long-running script
   has a fast-fail check at the top (creds, tables exist, paths exist)?
8. **Result objects vs raises.** Predictable failures return
   `SinkResult(status=...)`; exceptions reserved for catastrophic paths?

## Step 3 — Spec check

Per [`docs/standards/engagement.md`](../../../docs/standards/engagement.md):
- Is everything in the diff covered by the approved requirement?
- Any "while I was here" cleanups? → Flag them, ask whether to split.
- Any new abstractions/helpers the user didn't ask for? → Flag.

## Step 4 — Tests

- **Unit:** `poetry run pytest -m "not integration" -x` on the touched
  module path (`tests/test_<module>*`).
- **Contract tests:** if `app/services/<x>/` changed, run
  `tests/test_<x>*` for the contract surface.
- **Integration:** run **only if user requests it** (slow, needs live
  CH / S3 / provider creds).
- Report: pass count, fail count, skip count. Quote failures verbatim.

## Step 5 — Module-shape sanity

For changes under `app/services/<x>/`:
- Cross-service imports come from `schemas.py` / `contract.py`, never
  `service.py`?
- New service has the standard folder (`schemas/contract/service/tests/README`)?
- README updated in the same change if behavior or contract changed?

## Step 6 — Doc discipline

Per [`docs/standards/doc_discipline.md`](../../../docs/standards/doc_discipline.md):
- New service or contract change → `docs/ARCHITECTURE.md` updated?
- Storage/ingestion change → `docs/data_platform_plan.md` updated?
- Decision worth remembering → `docs/BUILD_JOURNAL.md` entry with date?

## Step 7 — Report

Output one block:

```
PREFLIGHT REPORT
================
Files touched:    <count> in <category>
Standards:        <PASS count> / <N/A count> / <FAIL count>  [list FAILs]
Spec coverage:    <ok | flagged: ...>
Tests:            <pass>/<fail>/<skip>  [list failures]
Module shape:     <ok | issues>
Doc updates:      <ok | missing: ...>
Mutation verify:  <ok | missing | n/a>

Recommendation:   <commit | fix listed items | surface tradeoff to user>
```

**Never run `git commit` automatically.** The user reviews the report
and gives the go.
