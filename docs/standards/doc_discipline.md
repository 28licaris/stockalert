# Doc Discipline

**Locked 2026-05-18.** Detailed commit messages are the build journal.
**Do not create `docs/BUILD_JOURNAL.md`** — it was retired on the lock
date and the file deleted 2026-05-21 (CV18). Historical entries are
preserved in the git history.

Why: the journal duplicated what the commit history already captured in
a more searchable form. Two writes for the same information cost more
than they paid back.

## Rules

1. **Every microservice folder has a `README.md`** — what it owns, its
   public contract, how to test it. **New folder → README in the same
   commit.**

2. **Plan docs update in the same change as the code.** Doc-to-change
   map:

   | Change                                | Update                                |
   |---------------------------------------|---------------------------------------|
   | New service / contract change         | [`ARCHITECTURE.md`](../ARCHITECTURE.md) |
   | Storage / ingestion change            | [`data_platform_plan.md`](../data_platform_plan.md) |
   | AI-trading change                     | [`trading-ai-build-plan.md`](../trading-ai-build-plan.md) |
   | Standards rule change                 | [`standards/`](README.md) (doc + index entry) |
   | Phase / gate / decision log           | **Commit message**, not BUILD_JOURNAL |

3. **Write the commit message like a journal entry.** Phase prefix,
   scope, files changed, gates passed, deferred follow-ups. The git log
   is the source of truth.

4. **When plan and code conflict, code wins.** (The "journal wins" rule
   is retired with BUILD_JOURNAL.)

## How this interacts with engagement

A plan doc does not pre-authorize an implementation choice — see
[`engagement.md`](engagement.md). The plan describes intent at a moment
in time; specific code changes still need explicit signoff.
