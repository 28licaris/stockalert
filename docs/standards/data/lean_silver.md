# Lean Silver — Minimum Viable Schema

Silver is the canonical hot path. Every byte costs storage; every column
costs cognitive overhead for every consumer; every extra piece of data
is a source-of-truth split waiting to happen.

## Rule

**Default to the minimum viable schema.** Derived information that can
be recomputed from canonical inputs is bloat — even when storage is
cheap.

Consumers compute derived views client-side. Centralizing recomputation
into a reader-side flag is also bloat — the math is 5 lines.

## Test before adding a column

> Can this be recomputed from existing columns + `silver.corp_actions`
> + bronze?

If yes: **don't add it**.

## Engagement half (locked 2026-05-18)

Plan docs do **not** pre-authorize schema choices. When a leaner vs
fuller option exists:

1. Surface the choice.
2. Recommend lean.
3. Wait for signoff.
4. Never silently pick the heavier option.

Locked after the `_raw` + `_adj` dual-column silver schema was added
unilaterally based on `silver_layer_plan.md §2.9`. Reverted 6 commits
later.

## Example

**Bad:** "I'll add a `_total_return` column too, documented in §2.10."

**Good:** "Silver adjustment: (a) split-adjusted close, or (b) split +
dividend total-return column. (a) is leaner; (b) saves clients from
recomputing. Which?"
