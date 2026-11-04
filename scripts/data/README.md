# scripts/data/

Small, hand-curated CSV seeds. Version-controlled on purpose: each row is
sourced from an official publication, and these are inputs that must not change
silently between runs.

| File | Holds | Source |
|---|---|---|
| `fomc_scheduled_meetings.csv` | FOMC meeting dates | federalreserve.gov |
| `cpi_release_dates.csv` | CPI release dates | BLS release schedule |
| `nfp_release_dates.csv` | Non-farm payroll release dates | BLS release schedule |

## Rules

- **No runtime scraping.** These exist precisely so the system never depends on
  parsing someone's HTML at request time.
- **Verify annually.** Release calendars are published a year ahead; extend
  these files from the official schedule and note the source in the commit
  message.
- **Not a data store.** Anything that grows, or that a job writes, belongs in
  ClickHouse or the lake — not here.

Consumed by the calendar and macro services; see
[`../../docs/data/market_calendar.md`](../../docs/data/market_calendar.md) and
[`../../docs/product/news_alerts.md`](../../docs/product/news_alerts.md).
The broader event seed lives at `data/market_events_seed.json`.
