# Scheduled tasks — versioned source

Verbatim copies of the Claude Code scheduled tasks that drive this repo.
They are checked in because both were **single points of failure**: each
existed only as one file in `~/.claude/scheduled-tasks/` on one laptop,
unversioned, while the repo depends on what they produce.

> **These copies do not run.** Claude Code reads scheduled tasks from the
> user-level directory. Whether it also discovers project-level ones is
> **unverified** — treat this directory as the backup and the source of
> truth for the *content*, not as a live installation.
>
> ```bash
> cp -R .claude/scheduled-tasks/<name> ~/.claude/scheduled-tasks/<name>
> ```
>
> Keep the two in sync by hand. If you edit the live copy, copy it back
> here in the same change — a backup that has silently drifted is worse
> than none, because it reads as authoritative.

## The tasks

| Task | Produces | Why losing it would hurt |
|---|---|---|
| [`daily-paper-trade-momentum`](daily-paper-trade-momentum/SKILL.md) | Advances the forward paper-trading record via `scripts/paper_daily_pipeline.py` | Forward paper is the **only honest track record** — backtests are not (see [`docs/research/findings.md`](../../docs/research/findings.md)). The record only advances because this runs daily. |
| [`ewt-email-scan`](ewt-email-scan/SKILL.md) | [`ewt/`](../../ewt/) — ticker analyses, index, structured extractions | The data is committed; this is the only record of **how** it is produced, and of the licensing rule that makes committing it defensible. |

## Operational knowledge that lives only in these files

Neither is just an invocation. Both carry interpretation rules that exist
nowhere else in the repo, which is most of the reason to version them:

- **`daily-paper-trade-momentum`** — which pipeline step legitimately
  reports SKIP (equities flat-files, lapsed subscription) versus a real
  failure; the EXP-34..36 context that momentum forward records are an
  honest-methodology falsification test expected to land near breakeven,
  **not** a customer-facing track record; and "if ClickHouse or
  credentials fail, report the error plainly — do NOT fabricate results."
- **`ewt-email-scan`** — "Quote nothing verbatim beyond short phrases;
  this is licensed subscriber content." That rule is why `ewt/raw/` holds
  ids, dates, symbols and timeframes rather than newsletter text, and it
  is what keeps the committed data defensible. Also: "Never place trades
  or infer a recommendation the analyst didn't state."

## Known issue — hardcoded path

`daily-paper-trade-momentum` hardcodes `cd /Users/licaris/dev/stockalert`.
That breaks on any other machine or checkout, and will have to be resolved
when the pipeline moves to AWS. Left as-is here **on purpose**: these are
backups, and a backup that differs from what is actually running is not a
backup. Tracked in [`docs/TODO.md`](../../docs/TODO.md).
