# components/

Shared React components, grouped by domain. A component that belongs to exactly
one page can live next to that route instead; anything reused, or carrying real
logic, belongs here.

## Areas

| Dir | Holds | Notes |
|---|---|---|
| [charts/](charts/README.md) | Candlestick chart, toolbar, indicator catalog, equity/trade chart | **Has its own README** — registry parity with the backend matters |
| [chat/](chat/README.md) | Assistant panel, composer, message rendering | **Has its own README** — SSE contract |
| [layout/](layout/) | `AppShell`, `Sidebar`, `Topbar`, `StatusBar`, `UserMenu`, `nav-items.ts` | Nav entries carry the `adminOnly` flag |
| [ui/](ui/) | Primitives (`button`, variants) | shadcn-style; keep unopinionated |
| [tables/](tables/) | `BarsTable`, `RoundTripsTable` | |
| [options/](options/) | `GexLadder`, `GexHistoryChart` | Dealer-gamma surfaces |
| [ppl/](ppl/) | `FanChart` | Posterior predictive level fan |
| [health/](health/) | `DataFreshnessPanel` | Reads the freshness probes from `app/services/health/` |
| [clickhouse/](clickhouse/) | `ResultsTable`, `SchemaSidebar` | Operator query console |
| [market/](market/) | `MarketBanner` | ClickHouse-native tape |
| [watchlists/](watchlists/) | `MyWatchlists` | |
| [symbol/](symbol/) | `SymbolSearchInput` | Debounced via `useDebouncedValue` |
| [brand/](brand/) | `LogoMark` | Visual system: [`docs/product/brand.md`](../../../docs/product/brand.md) |

## Conventions

- **No direct `fetch`.** Data comes from [`../api/`](../api/README.md) query
  hooks, so caching and error envelopes stay uniform.
- **No direct auth reads.** Use `useCurrentUser()`; gate operator surfaces with
  `AdminOnly` — see [`../auth/README.md`](../auth/README.md).
- **Operator-only components are hidden, not secured.** The server enforces
  `require_operator` independently; the UI guard is UX.
- **Tabs over stacking.** For parallel datasets (equities vs futures), use the
  repo's `role="tablist"` pattern rather than stacking tables.
- **Timezones are display-only** — go through
  [`../lib/timezone.ts`](../lib/timezone.ts), and don't confuse it with the
  backend's ET trading day.

A folder earns its own README when it has a contract worth stating (a backend
registry to stay in parity with, a streaming protocol). Otherwise this index is
the documentation.
