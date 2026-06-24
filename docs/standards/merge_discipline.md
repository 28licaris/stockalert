# Merge Discipline

**Rebase before merge. Verify the feature after merge.** A merge that
silently drops a feature still compiles and still passes unrelated
tests — the only way to catch it is to exercise the code that was
touched on *both* sides of the merge.

This standard exists because of a real incident (2026-06-23): the
`elliot-wave` and `codex/dashboard-auth` branches were merged into
`main` with plain merge commits and no rebase. Both branches had forked
from an older `main` and edited the same files (`OhlcvChart.tsx`,
`router.tsx`, `config.py`, `main_api.py`) in divergent ways. The
conflicts were resolved by taking one side wholesale, which dropped the
entire Elliott Wave feature — frontend routes, chart overlay, the
`routes_wave` registration, the config block, and the startup loops.
Nobody loaded `/app/ewt` after the merge, so it sat broken until a user
noticed. The botched merge also left three 856-line `main_api.py.backup`
/`.bak2`/`.pre-disable` junk files in the tree — the fingerprint of a
session thrashing on a conflict it couldn't reason through.

## Rules

1. **Rebase the feature branch onto current `main` before merging.**
   ```bash
   git checkout <feature>
   git fetch origin
   git rebase origin/main
   ```
   Rebasing replays each commit against the *real* current state, so
   conflicts surface small and legible (commit-by-commit) instead of as
   one all-or-nothing file blob. It does not make conflicts disappear —
   it makes them reviewable.

2. **Resolve conflicts by integrating both sides, never by picking a
   whole file.** When two branches both edited a file, the correct
   resolution almost always keeps *both* sets of changes. "Take theirs"
   / "take ours" on a file that diverged on both sides is how features
   get silently deleted. If you can't tell what each side intended,
   `git log -p <base>..<each-side> -- <file>` before resolving.

3. **After merging, smoke-test every surface the merge touched.**
   - Backend: `poetry run python -c "import app.main_api"`, start the
     app, and `curl` the endpoints the merged branches added or changed.
   - Frontend: `npx tsc --noEmit`, start the dev server, and load every
     page the merge touched (not just the homepage). A 200 on `/` proves
     nothing about `/app/ewt`.
   - Run the test suite — but treat it as necessary, not sufficient. A
     dropped feature with no failing test is the exact failure mode this
     standard guards against.

4. **No backup files in commits.** `*.backup`, `*.bak`, `*.bak2`,
   `*.orig`, `*.pre-disable`, `*.old`, `*.disabled` are merge debris.
   They never belong in a commit — use git history for old versions. If
   you see them in a diff you're reviewing, the merge was done badly;
   stop and redo it.

5. **The merge commit message lists what was verified.** Per
   [`doc_discipline.md`](doc_discipline.md), the commit message is the
   journal. For a merge, that means: which branches, which conflicting
   files, how each conflict was resolved, and which surfaces were
   smoke-tested. "Merged X" with no verification record is not enough.

## How this interacts with engagement

A merge is a code change like any other — [`engagement.md`](engagement.md)
applies. Resolving a conflict by guessing at intent is a silent design
decision; when the right resolution isn't obvious from both sides'
history, surface it rather than picking one and moving on.
