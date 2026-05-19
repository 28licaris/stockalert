# Engagement — Spec-first

**No code without an approved requirement or spec.**

This is the prime engagement rule for working on this repo. Restate the
ask, confirm scope, then write. Plans in `docs/` are guidance, not
pre-authorization.

## Why

Scope creep, "while I'm here" cleanups, and silent design choices have
cost more than they have ever saved. Repeated incidents:

- Adding a `_raw` + `_adj` dual-column silver schema based on a plan doc
  without surfacing it as a decision.
- Bundling unrelated cleanups into bug-fix PRs.
- Introducing helper abstractions that were never asked for.

The cost of asking once: one round-trip. The cost of guessing wrong: a
revert plus eroded trust.

## How to apply

### 1. Restate before writing

For any non-trivial task, lead with:

> I understand the ask as **X**, scope **Y**, touching files **Z** —
> confirm?

Even short tasks get a one-line restate when scope is ambiguous.

### 2. Plans are guidance, not authorization

[`BUILD_JOURNAL.md`](../BUILD_JOURNAL.md), `*_plan.md`, and runbooks
describe *intent*. They do not pre-approve any specific edit. Confirm
before acting on them.

### 3. Trivial edits proceed

- Typo fix.
- Single-variable rename the user just asked for.
- An obvious one-line bug fix the user explicitly requested.

The bar is: "would a reasonable engineer ask first?" If no, go.

### 4. Surface incidental issues — do not bundle them

If mid-task you find dead code, a related bug, or a stale doc: stop,
mention it, ask whether to expand scope or spawn a separate task. Never
silently include extra changes in a "fix X" PR.

### 5. Same rule for refactors, abstractions, new files, and doc edits

"I am adding a helper to deduplicate this" is a design choice that needs
signoff. So is "I am restructuring this docs section."

### 6. Ambiguous requirement → ask

Two readings of the same sentence = ask which one.

## What this is NOT

Not a paralysis rule. Once a spec is approved, execute end-to-end
without re-confirming every line. The check is at task boundaries, not
every keystroke.

## Self-test before committing

A task is mis-scoped if any of the following is true:

- The diff includes files the user did not mention or imply.
- The PR description starts with "Also fixed…"
- An abstraction the user did not ask for was introduced.

If any of those are true, the spec was not approved for what was
written. Stop, surface, ask.

## Related

- [`doc_discipline.md`](doc_discipline.md) — plans follow code, not the
  other way around.
- [`data/lean_silver.md`](data/lean_silver.md) — the canonical example
  of how silent scope expansion erodes a schema.
