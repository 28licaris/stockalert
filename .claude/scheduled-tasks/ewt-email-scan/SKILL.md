---
name: ewt-email-scan
description: EWT Email Scanning
---

# EWT Consensus Scan

Scan the inbox for new mail from ElliottWaveTrader (senders matching
*@elliottwavetrader.net, subjects containing "EWT", "Gilburt", "Wave
Setup", "Market Update"). Process anything received since the
timestamp in `ewt/state.json`.

For each message:
1. Read the body and every attachment (PDF, PNG/JPG charts, XLSX).
   Use the pdf-reading skill for PDFs; read chart images directly for
   labeled counts, pivots, and levels.
2. Extract per ticker:
   - primary wave count and degree, plus any alternate count
   - key levels: invalidation, pivot, target boxes, Fib zones
   - stated trade idea with direction, entry zone, stop, targets
   - analyst name, publish timestamp, timeframe (intraday, daily, weekly)
   - confidence language ("preferred", "alternate", "as long as we hold")
3. Append the extraction to `ewt/raw/<date>-<msgid>.json`. Never
   overwrite prior extractions.

Then write one file per symbol at `ewt/tickers/<SYMBOL>.md`, creating
it if the ticker is new. Each file holds:
- **Current count**: preferred count, degree, timeframe, date first
  adopted, analyst
- **Alternates**: each live alternate and the level that would promote it
- **Levels**: invalidation, pivots, targets, with how many separate
  updates cite each one
- **Trade ideas**: table of direction, entry, stop, targets, source
  date, status (pending, triggered, invalidated, closed)
- **Change log**: dated entries noting when a count flipped, what
  invalidated it, and which email drove it. Append only.

Also maintain `ewt/index.md`: one row per ticker with the current
preferred direction, days since last update, whether the count is
contested, and a link to the ticker file. Sort by most recently
updated.

Rules:
- Quote nothing verbatim beyond short phrases. Summarize in your own
  words; this is licensed subscriber content.
- If two updates conflict, keep both in the ticker file and flag it as
  contested in the index rather than picking a winner.
- Mark a ticker stale in the index if nothing has reconfirmed it in
  5 sessions.
- Never place trades or infer a recommendation the analyst didn't state.

Finish with a diff summary: tickers created, counts that flipped,
ideas that triggered or invalidated, and anything now contested.