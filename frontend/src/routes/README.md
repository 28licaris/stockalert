# routes/

One file per page. [`router.tsx`](router.tsx) is the route table.

## Pages

| Route | File | Audience |
|---|---|---|
| `/symbol/:ticker` | [symbol.tsx](symbol.tsx) | all — the main chart + analysis surface |
| `/watchlists` | [watchlists.tsx](watchlists.tsx) | all |
| `/options` · `/gex` | [options.tsx](options.tsx) · [gex.tsx](gex.tsx) | all — chains and dealer gamma |
| `/ppl` | [ppl.tsx](ppl.tsx) | all — posterior predictive levels |
| `/sectors` | [sectors.tsx](sectors.tsx) | all — RRG rotation |
| `/ewt` · `/ewt-guide` | [ewt.tsx](ewt.tsx) · [ewt-guide.tsx](ewt-guide.tsx) | all — Elliott Wave |
| `/backtest` · `/library` · `/paper` | [backtest.tsx](backtest.tsx) · [library.tsx](library.tsx) · [paper.tsx](paper.tsx) | all — strategy R&D + paper record |
| `/news` · `/economic` · `/calendar` | [news.tsx](news.tsx) · [economic.tsx](economic.tsx) · [calendar.tsx](calendar.tsx) | all |
| `/settings` | [settings.tsx](settings.tsx) | all |
| `/login` | [login.tsx](login.tsx) | unauthenticated |
| `/status` | [status.tsx](status.tsx) | **operator** — jobs, freshness, health |
| `/stream` | [stream.tsx](stream.tsx) | **operator** — streaming universe |
| `/clickhouse` | [clickhouse.tsx](clickhouse.tsx) | **operator** — query console |
| `*` | [not-found.tsx](not-found.tsx) | — |

## Conventions

- **Operator pages are gated twice.** Wrap in `AdminOnly` *and* mark the nav
  entry `adminOnly`; the server enforces `require_operator` independently. See
  [`../auth/README.md`](../auth/README.md).
- **A route composes, it does not fetch ad hoc.** Data comes from
  [`../api/`](../api/README.md) query hooks so caching and error envelopes stay
  uniform.
- **Prefer a tab over stacked tables.** When a page shows parallel datasets
  (equities vs futures), use the repo's `role="tablist"` pattern rather than
  stacking them and burying the second below the fold.
- **Timezones.** Render trading-day values through
  [`../lib/timezone.ts`](../lib/timezone.ts) — the trading day is ET
  04:00-20:00, so a UTC date misclassifies after-hours bars.
