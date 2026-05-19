# Doc Discipline

The doc layer in `docs/` is part of the build, not an afterthought.

Every microservice / module folder (under `app/services/`, future
`trading_ai/*`, or any service tree) has its own `README.md` describing
what the service does, what it owns, its public contract, and how to
test it.

> **New folder → README in the same commit, not later.**

## When to update which doc

When making an architectural change, update the relevant doc in the
**same change** as the code:

| Change                                | Doc to update                              |
|---------------------------------------|--------------------------------------------|
| New service or contract change        | [`ARCHITECTURE.md`](../ARCHITECTURE.md) service map + spec |
| Storage / ingestion change            | [`data_platform_plan.md`](../data_platform_plan.md) |
| AI-trading change                     | [`trading-ai-build-plan.md`](../trading-ai-build-plan.md) |
| Any decision with a reason            | [`BUILD_JOURNAL.md`](../BUILD_JOURNAL.md) decision log, today's date |
| Standards rule change                 | [`standards/`](README.md) — new/edited doc + index entry |

## Why

Future contributors (human and AI agent) pick up the build by reading
[`BUILD_JOURNAL.md`](../BUILD_JOURNAL.md) first. If the docs drift from
reality, that entry point misleads. Per-service READMEs prevent
contributors from having to read every file in a folder to understand
its role.

## How to apply

- Treat doc updates as part of the same task as the code change, not a
  follow-up. PRs that add a service without a README, or move
  architecture without updating the plans, are incomplete.

- When the [`BUILD_JOURNAL.md`](../BUILD_JOURNAL.md) and a plan doc
  conflict, the journal wins — it's the chronological record of what
  actually happened. The plan represents intent at a moment in time.

## Plans are guidance, not authorization

A plan doc does not pre-authorize an implementation choice. See
[`engagement.md`](engagement.md). The plan describes what we intend to
build; the spec for any specific code change still needs explicit user
signoff.

This is the same rule that produced the lean-silver standard — see
[`data/lean_silver.md`](data/lean_silver.md).

## Related

- [`engagement.md`](engagement.md) — spec-first.
- [`service_modules.md`](service_modules.md) — README is part of the
  folder template.
