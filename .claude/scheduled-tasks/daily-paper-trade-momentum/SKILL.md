---
name: daily-paper-trade-momentum
description: Daily paper pipeline: lake catch-up + ohlcv_daily refresh + all-strategy paper run + alerts
---

Daily paper-trading pipeline for EVERY strategy in the library, then report. Do exactly this:

1. Run this command (one line):
   cd /Users/licaris/dev/stockalert && AWS_PROFILE=stock-lake poetry run python scripts/paper_daily_pipeline.py

   Notes:
   - PR #20 is MERGED — run from the main tree (above). Do not use the old worktree paths.
   - The pipeline runs 3 steps and reports each: (a) equities flat-files lake catch-up — reports SKIP (intentionally disabled, subscription lapsed; expected, NOT a failure), (b) incremental ohlcv_daily refresh via Polygon REST grouped-daily (paced; raw archived to equities.polygon_daily_raw, split-adjusted slice appended to ClickHouse), (c) paper_run_all (re-runs every registered strategy's locked config, updates forward records, prints today's 🔔 ENTRY/EXIT signals). Takes ~30 min.
   - CONTEXT for reporting: per EXP-34..36 the momentum strategies' SIMULATED records use an optimistic fill model; their forward records are running as an honest-methodology falsification test (expectation ≈ breakeven), NOT as customer-facing track records. Report their numbers plainly without hype.
   - If step (b) is rate-limited it backs off automatically; if it fails outright, report plainly — momentum_top50* records simply don't advance that day. If ClickHouse or credentials fail, report the error plainly — do NOT fabricate results.

2. Report concisely (a daily heads-up, scannable):
   - Pipeline step status first ONLY for unexpected failures (the flat-files SKIP is expected; one short line is enough).
   - For EACH strategy: its name, forward return since go-live, current balance, and any 🔔 ENTRY/EXIT signals for today (these are the actionable alerts). Lead with strategies that have new signals.
   - If a strategy has no new signals, one line: "<name>: no new signals."

Do NOT place real trades — paper/alerts only.