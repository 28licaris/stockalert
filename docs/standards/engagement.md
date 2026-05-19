# Engagement — Spec-first

**No code without an approved requirement or spec.** Restate the ask,
confirm scope, then write.

## Rules

1. **Restate before writing.** "I understand the ask as X, scope Y,
   touching files Z — confirm?" Mandatory for non-trivial tasks.

2. **Plans are guidance, not authorization.** `*_plan.md`, runbooks,
   doc TODOs describe intent. They do not pre-approve any specific edit.

3. **Trivial edits proceed:** typo fix, the rename the user just asked
   for, the one-line bug fix the user explicitly requested. Bar:
   "would a reasonable engineer ask first?"

4. **Surface incidental issues — don't bundle.** Dead code, related
   bug, stale doc found mid-task → mention it, ask whether to expand
   scope or spawn a separate task.

5. **Refactors, new abstractions, new files, doc edits to architectural
   claims** all need signoff. "Adding a helper to dedupe this" is a
   design choice.

6. **Ambiguous → ask.** Two readings of the sentence = ask.

## Not a paralysis rule

Once a spec is approved, execute end-to-end without re-confirming every
line. The check is at task boundaries.

## Mis-scope self-test

Mis-scoped if any is true:

- Diff includes files the user didn't mention.
- PR description starts with "Also fixed…"
- An unrequested abstraction was introduced.

If yes: stop, surface, ask.
