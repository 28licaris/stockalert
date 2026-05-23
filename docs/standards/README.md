# Standards

The non-negotiable rules for this codebase. Every contributor (human or
AI agent) reads these. They are referenced from [`CLAUDE.md`](../../CLAUDE.md)
and load automatically into every Claude Code session.

These docs are **canonical**. When a plan doc, runbook, or comment
contradicts a standard here, the standard wins. Update the standard
(with the user's signoff) before changing how we work.

## Read these first

| Doc | When |
|-----|------|
| [`engagement.md`](engagement.md) | Before writing any code — the spec-first rule |
| [`coding.md`](coding.md) | Before any code change — the NO-SILENT-FAILURES rules |
| [`platform_design.md`](platform_design.md) | Before any architectural call |
| [`service_modules.md`](service_modules.md) | Before adding or editing a module in `app/services/` |
| [`testing.md`](testing.md) | Before adding or modifying tests |
| [`doc_discipline.md`](doc_discipline.md) | Before adding a service or changing architecture |

## Domain rules

| Doc | When |
|-----|------|
| [`trading_subsystem.md`](trading_subsystem.md) | Before touching `app/services/sim/*`, `app/services/sim/strategies/*`, or `app/indicators/*` |
| [`data/bronze_idempotency.md`](data/bronze_idempotency.md) | Before writing a bronze sink or maintenance script |
| [`data/lean_silver.md`](data/lean_silver.md) | Before adding a column to silver or any canonical schema |
| [`data/timezone_et_vs_utc.md`](data/timezone_et_vs_utc.md) | Before any trading-day math |
| [`data/athena_dialects.md`](data/athena_dialects.md) | Before writing Athena SQL |

## How to add a standard

A new standard is itself a design decision that needs explicit user
signoff (see [`engagement.md`](engagement.md)). The process:

1. Draft the rule + the *why* + the *how to apply* in a PR.
2. Get explicit signoff from the project owner.
3. Add it to this index in the correct section.
4. If the standard maps to a Claude Code memory pointer (under
   `~/.claude/projects/.../memory/`), update or create the pointer in
   the same PR.

## How to retire a standard

If a rule no longer applies (rare), open a PR that:
1. Removes the file.
2. Removes the row from this index.
3. Documents the date and reason in the commit message
   (per [`doc_discipline.md`](doc_discipline.md) — commit messages are
   the build journal).

Do not silently soften or delete a standard. Removing a rule is itself a
decision worth recording.
