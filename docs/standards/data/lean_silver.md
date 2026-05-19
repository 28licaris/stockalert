# Lean Silver — Minimum Viable Schema

Silver is the canonical hot path. Every byte costs storage, every column
costs cognitive overhead for every consumer, every extra piece of data
is a source-of-truth split waiting to happen.

## The rule

**Default to the minimum viable schema.** Storing derived information
that can be recomputed from canonical inputs is bloat, even when the
storage is cheap. The "self-contained row", "convenience", and "easy
debugging" arguments do not justify column doubling.

If a consumer needs a derived view (e.g. raw prices from adjusted +
splits), they compute it client-side from the canonical inputs.
Centralizing the recomputation into a reader-side flag is also bloat —
the use case is rare and the math is five lines.

## Test it

Before adding a column to silver, ask:

> Can this be recomputed from existing columns + `silver.corp_actions` +
> bronze?

If yes, **don't add it.**

## Why this rule exists

This was locked 2026-05-18 after a `_raw` + `_adj` dual-column silver
schema was added unilaterally based on
[`silver_layer_plan.md §2.9`](../../silver_layer_plan.md), without
surfacing the choice as an explicit decision. The user discovered the
dual-column schema ~6 commits later and pushed back; it was reverted.

The cost of asking once is one round-trip. The cost of bloated silver
is forever.

## How to apply

When designing or extending a silver schema:

### Bad

> "I'll add a `_total_return` column to silver too, in case someone
> wants dividend-adjusted prices. Documented in §2.10."

### Good

> "I'm about to design silver's adjustment story. Two options:
> (a) just split-adjusted close;
> (b) split-adjusted + a `_total_return` column that also folds in
> dividends.
> (a) is leaner but if you ever want true total-return backtests you'll
> need to recompute. Which do you want?"

Surface the choice. Recommend the lean version by default. Wait for
signoff. Never silently pick the heavier option and document it in a
plan doc as if it were already decided.

## Related

- [`../engagement.md`](../engagement.md) — spec-first; plans are
  guidance, not pre-authorization.
- [`../doc_discipline.md`](../doc_discipline.md) — docs follow code,
  not the other way around.
- [`bronze_idempotency.md`](bronze_idempotency.md) — bronze is the
  source of truth; silver is canonical-derived. Each tier earns its
  data.
- [`../service_modules.md`](../service_modules.md) — factories over
  inheritance, result objects over raises. Same spirit: don't add
  surface area you don't need.
