---
name: preflight-change
description: Run before committing a non-trivial change. Walks docs/standards/coding.md, runs targeted tests, verifies any cross-side data mutation. Trigger on "preflight" / "ready to commit" / "verify before push". Never auto-commits.
---

# Preflight

1. `git status` → `git diff --stat` → `git diff`. Classify: ingest /
   build / cron / scripts / app-services / app-api / tests / docs /
   config.

2. **Standards** — for each rule in
   [coding.md](../../../docs/standards/coding.md) §1–9, mark
   PASS / N/A / FAIL with a one-line reason.

3. **Spec** — per [engagement.md](../../../docs/standards/engagement.md):
   every diff file covered by approved spec? Any unrequested
   abstractions, "while I was here" cleanups? Flag if yes.

4. **Tests** — `poetry run pytest -m "not integration" -x` on touched
   modules (`tests/test_<module>*`). Integration only on request.

5. **Module shape** — for `app/services/<x>/`: cross-service imports
   come from `schemas.py` / `contract.py`; new service has the full
   folder + README.

6. **Docs** — per
   [doc_discipline.md](../../../docs/standards/doc_discipline.md):
   plans + READMEs updated if architecture changed. Write detail into
   the commit message (the journal is retired).

7. **Report**:

   ```
   Standards: PASS=N FAIL=N  [list FAILs]
   Spec:      ok | flagged: …
   Tests:     pass/fail/skip [list failures]
   Module:    ok | issues
   Mutation:  ok | missing | n/a
   Recommend: commit | fix listed | surface to user
   ```

**Never run `git commit` automatically.**
