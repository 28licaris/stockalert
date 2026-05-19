# Front-End Plan — The Developer's Cockpit

How we evolve the existing static HTML dashboard into a robust,
typed, component-driven single-page application that exposes
**every** capability of the platform — data, indicators, screener,
backtests, agents, MCP tools, monitoring — in one cohesive UI.

**Status:** FE-1, FE-1.5, FE-2 **landed** 2026-05-18. Stack decisions
locked — see §3.0 below. FE-2 ships the chart primitive scaffold;
FE-2.1 (indicator overlays, coverage strip, journal panel) is the
next follow-on. See [BUILD_JOURNAL.md](BUILD_JOURNAL.md) for the
gate evidence.

**Goal:** a developer-first cockpit built to **production-grade
modular standards** so it can graduate into a subscription product
later without a rewrite. For now you're the only user — but every
abstraction we introduce is designed so adding multi-tenancy, auth,
billing, and quotas is a *purely additive* change, not a rip-up.

This is the explicit two-mode contract:

| Mode | When | What's different |
|---|---|---|
| **Single-tenant dev mode** | Today | No login, no tenant context, your local machine, all data yours by construction |
| **Multi-tenant SaaS mode** | Future | Login wall, tenant-scoped everything, per-tenant quotas, billing, audit log |

The same codebase serves both. Every page, every API call, every
piece of persisted state goes through abstractions (`useCurrentUser`,
`tenantId`, `withQuota`) that are no-ops in dev mode and load-bearing
in SaaS mode. See §7 (SaaS-Readiness Contract) for the seams.

**Companion docs:**
- [frontend_api_contracts.md](frontend_api_contracts.md) — **READ THIS
  FIRST** for any new endpoint work. Inventories every cockpit-facing
  route, names the gaps, defines the contract rules, and parks the
  open architectural questions (watchlists-vs-streaming, `/api/v1`
  rename, etc.) that need signoff before FE-CONTRACTS-* phases start.
- [trading_subsystem_design.md](trading_subsystem_design.md) — strategy
  framework backing the Backtest + Runs pages.
- [data_platform_plan.md](data_platform_plan.md) — bronze/silver/gold
  surfaces backing the Lake + Coverage pages.
- [indicator_exposure_design.md](indicator_exposure_design.md) —
  IndicatorReader behind the Indicators + Symbol pages.
- [assistant_plan.md](assistant_plan.md) — the conversational
  cockpit copilot (Assistant drawer + `/assistant` page in §5).
- [ARCHITECTURE.md](ARCHITECTURE.md) — service map (the Status page
  visualizes this live).

---

## 1. Where we are today

Three static HTML files served by FastAPI as `FileResponse`:

| Page | Source | Stack | Lines | Calls (`fetch`) |
|---|---|---:|---:|---:|
| `/dashboard` | [app/static/dashboard.html](../app/static/dashboard.html) | Alpine.js + Tailwind CDN + Lightweight Charts | 41,099 | 2 |
| `/symbol/{ticker}` | [app/static/symbol.html](../app/static/symbol.html) | Alpine.js + Tailwind CDN + Lightweight Charts | 30,525 | 2 |
| `/journal` | [app/static/journal.html](../app/static/journal.html) | Alpine.js + Tailwind CDN | 22,368 | 5 |

What's exposed in UI today: market tape, watchlist, recent signals,
basic symbol chart, journal sync, daily P&L, monitor controls.

**What's NOT exposed but should be:**

| Backend capability | API surface | UI today |
|---|---|---|
| Backtest runner | `POST /api/backtest` (TA-1+) | none |
| Screener | `POST /api/screener/scan` (TA-4.3) | none |
| Indicator series + chart data | `GET /api/indicators/series`, `POST /api/indicators/chart-data` (TA-3.2) | partial |
| Iceberg bronze browser | `GET /api/lake/bars`, `GET /api/lake/symbols`, `GET /api/lake/last-day` | none |
| Bronze coverage / gap heatmap | `GET /api/backfill/coverage`, `GET /api/backfill/gaps` | gap count only |
| Backfill controls (5 modes) | `POST /api/backfill{,/deep,/daily,/intraday,/gaps}` | none |
| Movers | `GET /api/movers` | none |
| Instruments search | `GET /api/instruments/search` | none |
| MCP tool introspection + invocation | 29 MCP tools on `/mcp` | none |
| Strategy run history (`agent_runs`) | MCP `list_strategy_runs` only | none |
| Multiple watchlists CRUD | `GET/POST /api/watchlists`, `/api/watchlists/{name}/members` | single default watchlist only |
| Service health / startup | nothing dedicated (just `/health`) | one bool flag in header |

The static-HTML approach has hit its scaling ceiling. The dashboard
is ~41k lines of inline Alpine.js — every new feature compounds the
mess, there's no type safety against the (Pydantic-typed) backend,
no test coverage, no shared components, and adding npm libraries
requires hand-vendoring scripts via CDN URLs.

---

## 2. Goals & non-goals

### Goals

1. **Cover every backend capability.** If it's a Pydantic schema on
   the backend, there's a page or panel for it.
2. **Typed end-to-end.** API client and React components share types
   generated from the FastAPI OpenAPI schema. A backend schema change
   breaks the frontend build, not production.
3. **Density + speed.** Keyboard-first; command palette; dense
   tables with sortable columns; no marketing fluff. Bloomberg
   terminal feel over Robinhood polish.
4. **Live by default.** A single WebSocket pushes everything that
   changes (signals, bars, monitor state, ingestion progress); the
   UI never polls when push is available.
5. **Lift-out friendly.** The frontend lives in `frontend/` (a sibling
   of `app/`), runs via Vite in dev, builds to `app/static/dist/` for
   production. The day we split front-end and back-end into separate
   containers, that's an nginx config change, not a refactor.
6. **MCP-introspective.** First-class UI for listing MCP tools,
   inspecting their schemas, and invoking any of them with
   form-generated args — this is how the agent surface stays
   debuggable as it grows.

### Non-goals (explicit) — for now

- **Don't build a marketing/landing page yet.** Defer until SaaS
  gating. The cockpit IS the product surface today.
- **Don't optimize mobile-first.** Desktop is the dev target. The
  layout primitives we pick (CSS grid, container queries, sidebar
  drawer pattern) are mobile-capable so the future SaaS push doesn't
  re-architect anything; we just don't *invest* in mobile until users
  appear.
- **Don't implement auth UI / login flow.** But — **DO** scaffold
  the auth seam (`useCurrentUser` hook, FastAPI dependency
  injection, tenant-scoped DB queries). See §7. Adding a real auth
  provider later becomes wiring up the seam, not rewriting pages.
- **Don't implement billing or quota enforcement.** Same logic — the
  *seam* exists (every long-running operation flows through a
  `withQuota` decorator that's a no-op today); the enforcement is
  the SaaS-mode flip.
- **Don't pursue strict accessibility / WCAG audit.** WCAG-AA color
  contrast + keyboard navigation are free side-effects of shadcn/ui
  + Radix; we get them without effort. Screen-reader testing waits
  for real users.
- **No SSR / Next.js.** API-backed cockpit; no SEO. Adding SSR
  doubles ops complexity for zero benefit. If we ever need a
  marketing/landing site, that's a *separate* Next.js site at the
  marketing subdomain — the cockpit SPA stays its own deployable.

---

## 3. Stack decisions

### 3.0 LOCKED — 2026-05-18

The table below in §3.1 reflects the *original recommendation*. Before
FE-1 started, two swaps were made after reviewing the trade-offs with
the operator (who is not a frontend dev, will lean on AI/Stack Overflow
for help). Ubiquity > bleeding-edge where it doesn't matter.

| Concern | Plan recommendation | LOCKED choice | Why the swap |
|---|---|---|---|
| Routing | TanStack Router | **React Router v7** | React Router v7 has type-safe routes too (added in v7); 10× more Stack Overflow answers; what most React tutorials assume. TanStack Router is newer/smaller; we don't need its marginal type benefits enough to pay the ecosystem-size tax. |
| Lint/format | Biome | **ESLint + Prettier** | Biome is faster but React-rule coverage is still catching up. ESLint+Prettier are universal — every editor, every CI template, every AI assistant knows them cold. |

Everything else in §3.1 is unchanged.

The other locked-in decisions (recorded for posterity so future-us
doesn't relitigate them):

- **SaaS-readiness seams: INCLUDE in FE-1** (§7). Adds ~1 day now to
  avoid a 6–8 week refactor later if the subscription product happens.
- **Order: PARALLEL with TA-5 silver work.** The Status page makes
  silver build progress observable as it lands.
- **Folder: `frontend/`** at repo root (sibling of `app/`). Self-
  contained, zero imports from `app/`, lift-out to its own repo is a
  `git mv` + CORS config change.
- **Mobile posture: responsive-by-default web app today; PWA install
  when SaaS launches; separate React Native companion app later if and
  only if mobile monitoring becomes a real product.** A single codebase
  trying to be both desktop cockpit and mobile app is a trap — Bloomberg
  ambitions don't survive 375px viewports. Tailwind + container queries
  give us "looks fine on phone" for free; native mobile is a different
  product surface, not a stretch goal of this one.

### 3.1 Original recommendation (reference)

After surveying the field, the original recommendation:

| Concern | Recommendation | Why this and not the alternatives |
|---|---|---|
| Framework | **React 18 + TypeScript** | Largest financial-widget ecosystem (Lightweight Charts has first-class React bindings); broadest hireable knowledge if you ever want help; type system catches the most bugs. Considered Svelte (smaller bundles, lower ceiling for our complexity) and Vue (good but smaller financial-widget ecosystem). |
| Build | **Vite 5** | Fastest dev HMR I've used; minimal config; the new default for any React-without-SSR app. Not Webpack (slow), not Parcel (less library support). |
| Routing | **TanStack Router** | Type-safe routes derived from file paths; works with code-splitting; better than React Router for typed cockpit apps. React Router would also be fine — flag for revisit if TanStack churns. |
| Server state | **TanStack Query (React Query)** | Caching, deduplication, polling, optimistic updates, WS integration. The standard. Without this you reinvent it badly. |
| Client state | **Zustand** | Minimal, no Redux ceremony. Used only for cross-page UI state (theme, sidebar, command palette). All server state goes through TanStack Query. |
| Component library | **shadcn/ui** (Radix + Tailwind, code-copied not npm'd) | We own every component file → infinite customization. The "Bloomberg terminal" aesthetic we want isn't off-the-shelf in any kit. Considered Mantine (good, but theming lock-in) and Material UI (wrong aesthetic, heavy). |
| Tables | **TanStack Table** | Headless, virtualized, sortable, filterable. The benchmark for dense data tables. |
| Charts (financial) | **Lightweight Charts** (already in use) | TradingView's; what we already render with; first-class React bindings. |
| Charts (general) | **Recharts** | For equity curves, metric dashboards, non-OHLCV. Simple, React-native, good defaults. |
| Forms | **react-hook-form + Zod** | Zod schemas can MIRROR our Pydantic models. The end-to-end type chain becomes Pydantic → OpenAPI → openapi-typescript → Zod → React Hook Form. Type-safety boundary collapses to zero. |
| API codegen | **openapi-typescript** + **openapi-fetch** | Reads FastAPI's `/openapi.json`, emits typed client. Generate-on-build (no manual regeneration). Considered the heavier `openapi-generator` — it produces too much code; openapi-typescript is the modern minimal pick. |
| Styling | **Tailwind 3** (proper install, not CDN) | We already use Tailwind. The CDN version blocks dev tooling — installing properly unlocks Tailwind plugins + IntelliSense. |
| State persistence | **localStorage** via Zustand `persist` | For panel layouts, recently-used symbols, screener spec drafts. No backend involvement needed. |
| Testing | **Vitest + Testing Library + Playwright** | Vitest = jest-compatible, Vite-native. Testing Library for component tests. Playwright for the handful of end-to-end smokes (run a screener, see results). |
| Lint / format | **biome** | Single tool replacing ESLint + Prettier. 10× faster, one config file. (Could fall back to ESLint+Prettier if biome's React support hits limits — easy swap.) |
| Icons | **lucide-react** | Open-source, large set, tree-shaken. |

### Strong alternative for consideration: **HTMX + Jinja**

If the React stack feels heavy, the legitimate alternative is
HTMX + server-rendered Jinja templates from FastAPI. Pros:
no JS framework, no build step, no codegen, no separate frontend
folder. Cons: weaker for the "cockpit" patterns we want (command
palette, persistent global state, optimistic updates on
long-running operations like backtests), and harder to extract into
its own deployable when the time comes. **For a Bloomberg-terminal
ambition, React wins. For a "make the existing dashboard cleaner"
ambition, HTMX wins.** Flagging — your call, plan below assumes React.

---

## 4. Proposed architecture

### 4.1 Repo layout

```
/Users/licaris/dev/stockalert/
├── app/                                    # existing FastAPI backend
│   ├── api/
│   ├── services/
│   └── static/                             # ← new build artifact dir
│       └── dist/                           # Vite produces here; served at /
├── frontend/                               # ← NEW
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── biome.json
│   ├── index.html
│   ├── public/                             # static assets (favicon, ...)
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx                         # router root
│   │   ├── routes/                         # TanStack Router file-based routes
│   │   │   ├── __root.tsx                  # layout (sidebar + topbar)
│   │   │   ├── index.tsx                   # /  → Status / overview
│   │   │   ├── symbol.$ticker.tsx          # /symbol/AAPL
│   │   │   ├── screener.tsx
│   │   │   ├── backtest.tsx
│   │   │   ├── indicators.tsx
│   │   │   ├── lake.tsx
│   │   │   ├── coverage.tsx
│   │   │   ├── runs.tsx
│   │   │   ├── journal.tsx
│   │   │   ├── monitors.tsx
│   │   │   ├── watchlists.tsx
│   │   │   ├── mcp.tsx                     # MCP tool explorer + invoker
│   │   │   └── settings.tsx
│   │   ├── api/
│   │   │   ├── client.ts                   # openapi-fetch instance
│   │   │   ├── types.gen.ts                # AUTO-GENERATED from /openapi.json
│   │   │   ├── queries.ts                  # TanStack Query hooks (per endpoint)
│   │   │   └── ws.ts                       # WebSocket subscription manager
│   │   ├── components/
│   │   │   ├── ui/                         # shadcn/ui primitives (button, dialog, …)
│   │   │   ├── layout/                     # Sidebar, Topbar, StatusBar
│   │   │   ├── charts/
│   │   │   │   ├── OhlcvChart.tsx          # Lightweight Charts wrapper
│   │   │   │   ├── EquityCurve.tsx         # Recharts
│   │   │   │   └── CoverageHeatmap.tsx     # Recharts/d3
│   │   │   ├── tables/
│   │   │   │   ├── DataTable.tsx           # TanStack Table generic
│   │   │   │   ├── SignalsTable.tsx
│   │   │   │   ├── CandidatesTable.tsx
│   │   │   │   ├── BarsTable.tsx
│   │   │   │   └── RunsTable.tsx
│   │   │   ├── builders/
│   │   │   │   ├── ScreenerSpecBuilder.tsx # Pydantic→Zod→form builder
│   │   │   │   └── BacktestConfigBuilder.tsx
│   │   │   ├── command-palette/            # ⌘K palette (jump-anywhere)
│   │   │   ├── status/                     # health badges, freshness pills
│   │   │   └── widgets/                    # composed page-level widgets
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   ├── useKeyboardShortcuts.ts
│   │   │   └── useRecentSymbols.ts
│   │   ├── lib/
│   │   │   ├── fmt.ts                      # price, pct, ts formatters
│   │   │   ├── colors.ts                   # consistent indicator/signal colors
│   │   │   └── const.ts
│   │   ├── store/
│   │   │   └── ui.ts                       # Zustand: theme, sidebar, etc.
│   │   └── styles/
│   │       └── globals.css
│   └── tests/
│       ├── unit/
│       └── e2e/
└── pyproject.toml
```

### 4.2 How the frontend talks to the backend

```
                      ┌─────────────────────────────────┐
                      │  FastAPI (app/main_api.py)      │
                      │  ───────────────────────────    │
                      │  Pydantic schemas               │
                      │  ↓                              │
                      │  41 HTTP endpoints              │
                      │  29 MCP tools (at /mcp)         │
                      │  1 WebSocket (/ws/signals)      │
                      │  /openapi.json                  │
                      └────────────┬────────────────────┘
                                   │
              ┌────────────────────┼─────────────────────┐
              │                    │                     │
       OpenAPI schema         REST calls           WebSocket
        (build time)          (runtime)             (runtime)
              │                    │                     │
              ▼                    ▼                     ▼
  ┌────────────────────┐  ┌──────────────────┐  ┌────────────────────┐
  │ openapi-typescript │  │  openapi-fetch   │  │ native WebSocket   │
  │ → types.gen.ts     │  │  → typed client  │  │ + custom manager   │
  └────────────────────┘  └──────────────────┘  └────────────────────┘
              │                    │                     │
              └────────────────────┼─────────────────────┘
                                   ▼
                       ┌───────────────────────┐
                       │  TanStack Query       │
                       │  (cache, dedup, poll) │
                       └───────────────────────┘
                                   │
                                   ▼
                       ┌───────────────────────┐
                       │  React Components     │
                       └───────────────────────┘
```

**The type chain is closed:** Pydantic → OpenAPI → TypeScript →
components. A backend schema change makes the frontend build red,
catching the integration break at CI time rather than runtime.

### 4.3 Build + deployment

- **Dev mode:** `cd frontend && npm run dev` starts Vite on port
  5173 with HMR; Vite proxies `/api`, `/mcp`, `/ws/*` → `http://localhost:8000`
  (the FastAPI process). Type generation runs in watch mode against
  `/openapi.json`.
- **Production:** `cd frontend && npm run build` produces
  `app/static/dist/`. FastAPI's existing static-mount serves it.
  `/` redirects to `/app` (the SPA shell); `/legacy/dashboard` stays
  available pointing at the old `dashboard.html` for the transition
  period.
- **CI:** GitHub Actions adds a `frontend-build` job. Fails on
  TS errors, lint errors, or test failures. Same image; npm install
  + vite build run as Docker build steps.
- **Versioning:** the SPA bundle hash gets exposed as
  `/api/health/version` so we can verify which build is deployed.

### 4.4 The legacy bridge

The existing static HTML pages (dashboard, symbol, journal) keep
working unchanged at `/legacy/*` routes for the entire transition.
We don't delete them until the React version of every page reaches
**parity + 1** (parity meaning "everything the old page does, the new
one does" + one new capability the legacy version can't deliver).
This is the discipline that turns a "rewrite" into a "migration."

---

## 5. The page catalog (the cockpit)

Each page is one TanStack Router file under `frontend/src/routes/`.
Listed roughly in priority order, with the existing static-HTML
predecessor and the new capabilities it unlocks:

### 5.1 `/` — Status (NEW)

System-wide health at a glance. Replaces the static dashboard's
single ClickHouse/Stream pill with a dense, live view of every
subsystem.

- ClickHouse, Iceberg/Glue, S3, Schwab, Polygon health (color pills).
- **Silver-build health:** last-successful-build age (target <24h),
  coverage % of watchlist symbols (target 100%) — pulled from
  `silver.bar_quality`. See [silver_layer_plan.md §9.5](silver_layer_plan.md).
- Live ingestion rate per source (bars/sec by provider).
- Bronze + silver freshness per symbol (heatmap).
- Backfill queue: in-flight jobs + ETA.
- **"Recently added symbols, warming up"** widget — one row per
  symbol added in the last 5 min, with the silver→CH backfill
  progress (target completion <30s; see §5.2 below).
- Monitor service: started monitors, signal rate, error counts.
- Service-map mini-diagram (read from `docs/ARCHITECTURE.md` service list).
- Live "log tail" stream over WS (last 50 INFO/ERROR lines from
  the FastAPI logger).

Powered by: `/health`, `/stats`, `/api/backfill/status`,
`/monitors`, `/api/lake/last-day` (existing); + 1 new endpoint
`/api/health/services` (composite); + new `/api/silver/health`
(after silver lands).

### 5.2 `/symbol/{ticker}` — Symbol (PARITY + extensions + warming-up UX)

Successor to `symbol.html`. OHLCV candlestick + indicator overlay
+ signals/divergence + Iceberg-silver coverage strip + journal
trades on that ticker.

- Interval picker (1m..1d).
- Indicator panel: stack any indicator from `/api/indicators/series`
  with live overlay (SMA, EMA, RSI, MACD, ATR, Bollinger, …).
- Signal markers (regular + hidden divergence) on chart.
- Recent bars table beneath chart (TanStack Table).
- Coverage strip (per-day green/yellow/red bar count vs expected).
- Journal trades on this ticker (in-page).
- "Open in MCP" button → jumps to MCP page pre-populated with
  `get_chart_data(symbol=…)`.
- **Adjusted/raw toggle.** Default: adjusted prices (silver
  `_adj` columns). Toggle to raw (`_raw`) for "what the trader
  saw live" inspection. See
  [silver_layer_plan.md §14.2](silver_layer_plan.md).

**Warming-up state.** Branches by whether the symbol is in the
seed universe (silver_layer_plan §2.3):

- **Seed-universe symbol** (full silver history exists): chart area
  renders a progress card. Shows live-stream subscription status,
  silver→CH backfill progress (~10s ETA), bars-loaded count.
  Card swaps to chart when bars are sufficient. Source label:
  "Backfilling from silver (Iceberg) — your own data, no provider
  API calls."

- **Ad-hoc symbol** (NOT in seed universe; silver has no history
  yet): chart area shows a banner: "Exploration mode — fetching
  ~48 days from Schwab REST. Promote to seed universe for deeper
  history." Live ticks appear immediately as overlays; Schwab REST
  history fills in over ~30s. After the next nightly silver_build,
  the chart automatically switches to silver-derived data.

Live ticks arriving before the historical backfill completes are
buffered (`bar_buffer` in the Symbol page state) and prepended
only when the historical context behind them is loaded. No
floating lonely candles.

**"Promote to seed" button** on the Symbol page header for ad-hoc
symbols. Clicking it calls `POST /api/seed/promote` which:
1. Adds the symbol to `settings.seed_symbols`.
2. Kicks off a deeper one-shot backfill (Polygon flat-files if
   subscribed; Schwab REST otherwise).
3. Marks the symbol for nightly refresh inclusion.

The button hides once the symbol is in seed.

Powered by: existing routes; one new combined
`/api/symbol/{ticker}/overview` to reduce roundtrips on first
paint; new `/api/backfill/silver-to-ch/progress` for the
warming-up card (after silver_to_ch backfill mode lands —
[silver_layer_plan.md §6](silver_layer_plan.md)).

### 5.3 `/screener` — Screener (NEW)

Visual builder for `ScreenerSpec`. Live-runs the scan. Each
candidate gets a mini sparkline.

- LHS: spec builder (universe / interval / rules / rank).
  - "Add rule" dropdown showing the 13 RuleKinds with form fields
    auto-generated from the Pydantic schema's `params`.
  - Save spec drafts to localStorage.
  - Sample-specs library (the example specs from
    `app/services/screener/README.md`).
- RHS: candidates table with sparklines.
  - Click candidate → opens `/symbol/<ticker>` in a side panel.
- Result diagnostics: universe size, n passed, errors list.

Powered by: `POST /api/screener/scan` (TA-4.3, just landed).

### 5.4 `/backtest` — Backtest (NEW)

Run a backtest from the UI. Show the equity curve, trade log,
metrics, write to `agent_runs` registry.

- LHS: strategy picker + parameter form (each strategy's
  `Params` Pydantic class auto-generates the form via Zod).
- Universe, interval, date range, starting cash, fees model.
- "Run" button → POSTs to `/api/backtest`, streams progress via WS
  if available, shows equity curve + drawdown + metrics on completion.
- "Save as agent run" button → writes to `agent_runs` (also auto
  on run completion for reproducibility).
- "Replay this run" button on completed runs → re-executes with
  pinned snapshot_id to verify determinism.

Powered by: `POST /api/backtest`, the MCP `run_backtest` tool, and
`list_strategy_runs` (existing).

### 5.5 `/indicators` — Indicators (PARTIAL → PARITY)

Multi-symbol, multi-indicator comparison. Currently the only
indicator UI is on the Symbol page.

- Pick symbols (multi), pick indicators (multi), pick interval.
- Grid view: rows = symbols, columns = indicators.
- Hover any cell → detail popover (full series chart).
- "Send to chart" sends an indicator series to overlay on a
  symbol chart.

Powered by: `GET /api/indicators/series`, `POST /api/indicators/chart-data`.

### 5.6 `/lake` — Iceberg Lake Browser (NEW)

Browse the bronze tier. List tables, view snapshots, view per-symbol
bar counts per day, run ad-hoc PyIceberg queries.

- Catalog browser: `bronze.polygon_minute` etc. with row count,
  snapshot count, last write time.
- Per-symbol bar count over time (heatmap).
- Snapshot history per table — click snapshot → diff against latest
  (rows added/removed per day).
- Ad-hoc PyIceberg query box (read-only; result table; no DML).

Powered by: `GET /api/lake/symbols`, `GET /api/lake/bars`,
`GET /api/lake/last-day`, MCP `get_bronze_table_stats`,
`get_lake_freshness`.

### 5.7 `/coverage` — Coverage / Gaps (PARTIAL → PARITY)

Per-symbol bar-coverage heatmap. Find and fill gaps.

- Heatmap: rows = symbols, columns = days, color = % of expected bars.
- Click cell → bar-by-bar detail for that (symbol, day).
- "Backfill this gap" → POSTs to `/api/backfill/gaps` with the
  resolved window.
- "Backfill all red cells" bulk action.

Powered by: `GET /api/backfill/coverage`, `GET /api/backfill/gaps`,
`POST /api/backfill/gaps`.

### 5.8 `/runs` — Strategy Runs (NEW)

Browse the `agent_runs` ClickHouse table. Reproducibility audit.

- Sortable table of runs: strategy, version, symbols, window,
  return, Sharpe, max DD, n_trades, snapshot_id, git_sha.
- Filter by strategy name, by symbol, by date range.
- Click run → expanded view: full metrics + trade log + config.
- "Replay" button → re-runs the same `(strategy_version, config, snapshot_id)`
  triple and asserts identical metrics row (the reproducibility
  test from the strategy framework).
- Side-by-side compare of any 2 runs (metrics diff, equity-curve
  overlay).

Powered by: MCP `list_strategy_runs` + new `GET /api/runs` HTTP
route (this becomes the first HTTP-side replication of an MCP-only
read; same `RunMetrics` schema both surfaces).

### 5.9 `/journal` — Trade Journal (PARITY)

Successor to `journal.html`. Same functionality, polished.

Powered by: existing `/api/journal/*` routes.

### 5.10 `/monitors` — Monitors (PARTIAL → PARITY)

Currently exposed via dashboard sidebar. Promote to full page.

- Per-symbol monitor state, signal rate, error rate.
- Start/stop monitors individually.
- View detector config per monitor (read-only; settings are env).

### 5.11 `/watchlists` — Watchlists (PARTIAL → PARITY)

CRUD on multiple watchlists. Drag-drop member management.

- List all active watchlists.
- Create/rename/delete (soft).
- Add/remove members (with normalization preview — `mnqm26` → `/MNQM26`).
- "Use as screener universe" button.
- Members table includes live last-price column (the market banner
  data per member).

Powered by: full `/api/watchlists/*` route family (existing but
unused by current UI).

### 5.12 `/mcp` — MCP Tool Explorer (NEW; agent dev surface)

This is the unique feature that justifies the whole cockpit. List
every MCP tool. Inspect its schema. Invoke it with form-generated
args. See the typed response rendered as JSON or auto-rendered
(charts for chart-data, tables for bar lists, etc.).

- Sidebar: tree of 29 MCP tools by category (lake, live, sim,
  indicators, screener, …).
- Main: selected tool's JSON-schema input rendered as form,
  output renderer auto-picked from the response shape.
- Recent invocations log (localStorage, last 50).
- "Replay" any previous invocation.
- Direct interrogation: simulate what an LLM agent sees.

This page becomes the truth surface for "what can my agent see?"
during agent development. As 29 tools grow to 60+ over the next
phases (silver readers, EW state, RL controls), having one place to
introspect them is the difference between "agent dev is tractable"
and "agent dev is dark archaeology."

Powered by: MCP `tools/list` + `tools/call` via the JSON-RPC
transport at `/mcp`.

### 5.13 `/assistant` — Conversational Assistant (NEW)

Natural-language interface to the entire platform. Two surfaces
sharing one component family:

- **Global drawer** (`<AssistantDrawer />`): slides in from the
  right on any cockpit page; pre-populates with page context
  (current symbol, interval, selected entity).
- **Dedicated page** (`/assistant`): long sessions, conversation
  browser, branch/regenerate UX, daily-spend widget.

Capabilities (phase 1):

- Token-streamed answers grounded in MCP tool calls — no invented
  data. The "Why?" link on every claim opens a panel with the
  exact tool calls + results that grounded it.
- Read-only tools auto-execute; **write tools (e.g. `run_backtest`)
  require confirm-before-mutate** with a plain-English summary card.
- Inline rich artifacts: equity curves, OHLCV charts, screener
  tables, coverage heatmaps — rendered via the **same React
  components** used elsewhere in the cockpit. One render path.
- Slash commands (`/symbol`, `/screener`, `/backtest`, `/explain`)
  and `@mentions` (watchlists, signals, runs) as structured context.
- Conversation history persisted to ClickHouse, scoped by
  `owner_id` (per §7.1); soft-delete; branch from any turn.
- Cost/quota footer per turn; daily-spend widget on the dedicated
  page; flows through `useQuotaMutation` (§7.6).

Powered by: a new bounded service `app/services/assistant/`
(Anthropic SDK + MCP client + conversation store + response cache).
Endpoints under `/cockpit/assistant/*` (UI-internal per §7.4);
streams via SSE.

**Distinct from the trading `LLMAgent`** in
[trading-ai-build-plan.md](trading-ai-build-plan.md): that one is
autonomous, per-bar, output is structured `{action, size_pct, …}`
JSON, and lives in `app/services/sim/strategies/llm_agent.py`.
This assistant is interactive, user-driven, and cannot route
orders. See [assistant_plan.md §3.2](assistant_plan.md) for the
full disambiguation.

Full spec, contracts, and phasing (AS-1 … AS-8) in
[assistant_plan.md](assistant_plan.md).

### 5.14 `/settings` — Settings (NEW)

UI-only settings (panel layouts, theme, default interval, default
universe). Plus a read-only view of the `app/config/settings.py`
runtime values that don't contain secrets.

---

## 6. Cross-cutting features

### 6.1 Command palette (`⌘K`)

Jump-anywhere. Fuzzy match against:
- All pages
- All ticker symbols in any watchlist
- All MCP tools
- Saved screener specs
- Saved backtest configs
- Recent agent runs

This is the developer-cockpit signature. One keystroke, three
characters, you're anywhere.

### 6.2 Keyboard shortcuts

- `gd` / `gs` / `gb` / `gm` — go to dashboard / screener / backtest / MCP
- `/` — focus the ticker search
- `?` — keyboard shortcut help overlay
- `Esc` — close modal / drawer

### 6.3 Theme

Dark by default (matches current static dashboard). Light theme
optional but lower priority. Color tokens encoded via Tailwind
CSS variables so component-level theming is automatic.

### 6.4 Real-time channel (one WS, many topics)

Currently we have `/ws/signals` only. Plan:

- Promote to `/ws/events` (versioned, generic).
- Topic-based subscriptions: `signals`, `bars.{symbol}`,
  `monitors.{symbol}`, `backfill.progress`, `backtest.progress`,
  `mcp.invocations`.
- Frontend `useWebSocket(topic)` hook integrates with TanStack
  Query so receiving a `bars.AAPL` push invalidates the relevant
  query cache automatically.
- No polling for anything that emits push events.

### 6.5 Optimistic updates

For mutations (add watchlist member, start a monitor, fire a
backfill), TanStack Query optimistically updates the cache and
rolls back on server error. Cockpit feels snappier; failures are
loud.

### 6.6 Telemetry-style internal log panel

Bottom drawer with the last N log lines from the FastAPI process,
streamed via a new `/ws/logs` topic. Filterable by level + logger
name. Helps debug "why didn't this fire?" without `tail -f` in a
separate terminal.

---

## 7. SaaS-Readiness Contract

The single most important architectural commitment of this plan:
**every abstraction we use today is the same abstraction the SaaS
version uses.** Dev mode is "every value is `null` / `default` /
no-op" mode. SaaS mode is "values get populated by middleware."
No code path branches on `if SAAS_MODE`.

This section documents every seam. Adding multi-tenancy, auth,
billing, and per-tenant quotas later is a matter of *implementing*
each seam — not introducing new ones.

### 7.1 The single-tenant / multi-tenant boundary

Three categories of data + state. Each has a clear future migration:

| Category | Examples | Today | Future SaaS |
|---|---|---|---|
| **Platform-global** | Bronze tables, indicator math, MCP tool schemas, market hours, instrument catalog | Public to everyone | Public, unchanged |
| **Tenant-scoped** | Watchlists, screener spec drafts, backtest configs, journals, agent runs, MCP invocation history, UI prefs | One implicit "owner" (you) | Tagged with `tenant_id`; only visible to that tenant |
| **Per-user-in-tenant** | Last-viewed symbol, command-palette history, panel layout | Browser localStorage | Same + cross-device sync |

The plan: **tag the tenant-scoped category from day one** with an
explicit `owner_id` column on every relevant table. In dev mode the
column defaults to a single sentinel value (`"default"`). In SaaS
mode the auth middleware populates it.

### 7.2 Backend seams (FastAPI side)

Even though this is the *frontend* plan, the seams have to exist on
both sides. The corresponding backend work goes into the journal as
companion phase **TA-SaaS** (not yet scheduled, but spec'd here so
neither side surprises the other):

```python
# app/auth/principal.py  ← NEW
class Principal(BaseModel):
    """Who is making this request. In dev mode, always the default
    principal. In SaaS mode, derived from the auth middleware."""
    user_id: str = "default-user"
    tenant_id: str = "default-tenant"
    roles: list[str] = ["owner"]
    plan: str = "dev"  # 'dev' | 'free' | 'pro' | 'enterprise'

async def get_principal(request: Request) -> Principal:
    """FastAPI dependency. Today returns DEFAULT_PRINCIPAL.
    Tomorrow reads from session/JWT/etc."""
    return DEFAULT_PRINCIPAL
```

Every tenant-scoped route adds one dep:

```python
# app/api/routes_watchlist.py
@router.get("/watchlists")
def list_watchlists(principal: Principal = Depends(get_principal)):
    return watchlist_repo.list_watchlists(owner=principal.tenant_id)
```

Three rules that hold from day one:

1. **No tenant-scoped query lacks an owner filter.** Even today, with
   one tenant, every query filters by `owner_id="default-tenant"`. A
   lint check enforces this at PR time.
2. **The data layer accepts the owner column even on read.** Migration
   step at SaaS-time = backfill `owner_id` columns on existing rows
   + flip middleware on. Zero ORM/query rewrite.
3. **The Principal flows into MCP tools too.** MCP tools that touch
   tenant-scoped data (`list_strategy_runs`, `get_watchlist_members`)
   take a `principal` arg; today it's the default. The agent eventually
   gets a per-tenant agent identity in SaaS mode.

### 7.3 Frontend seams (React side)

```typescript
// frontend/src/auth/useCurrentUser.ts
export function useCurrentUser(): CurrentUser {
  // Today: returns DEV_USER. Tomorrow: reads from an auth context
  // wrapped around the router root.
  return DEV_USER;
}

// frontend/src/api/client.ts
export const apiClient = createClient<paths>({
  baseUrl: API_BASE,
  // Today: no-op. Tomorrow: adds Authorization header.
  fetch: withAuth(fetch),
});
```

Persisted state goes through one abstraction:

```typescript
// frontend/src/lib/storage.ts
// Today: localStorage with key `stockalert:{userId}:{key}`
// Tomorrow: cloud-synced via /api/me/prefs with same key
export function useUserSetting<T>(key: string, default: T): [T, (v: T) => void] {
  const user = useCurrentUser();
  // ...
}
```

Routes that need to be protected later are pre-marked:

```typescript
// frontend/src/routes/watchlists.tsx
export const Route = createFileRoute('/watchlists')({
  meta: { protected: true },   // ← no-op today, gates redirect tomorrow
  component: WatchlistsPage,
});
```

### 7.4 Public API vs Cockpit API separation

Two endpoint families, with explicit naming:

| Prefix | Audience | Stability | Versioning |
|---|---|---|---|
| `/api/v1/...` | **External**: the future SaaS REST API. Stable; semver. Documented OpenAPI. Auth required in SaaS mode. | High; breaking change = new major version | `/api/v1`, `/api/v2`, ... |
| `/cockpit/...` | **Internal**: the React SPA. Whatever shape is most efficient for the UI (composed responses, etc.). Same auth gating but UI-shaped, not API-product-shaped. | Medium; can break with cockpit deploy | None (deploys together with the SPA) |
| `/mcp/...` | **Agent surface**: MCP tools. Same Pydantic contracts as `/api/v1` to keep the agent surface a first-class citizen. | High; matches `/api/v1` | Matched to `/api/v1` |

Today we have only `/api/*`. The plan rebases existing routes onto
`/api/v1/*` (one-shot rename) and adds a new `/cockpit/*` family for
UI-composed endpoints (the "give me everything for the Symbol page
in one call" pattern) when needed. The MCP server already lives at
`/mcp` — kept as-is. The historical static-HTML pages keep working
behind `/legacy/*`.

**Why this matters:** when SaaS lands, you don't have to retroactively
classify which endpoints are public, which are cockpit-internal, and
which are agent. They're already separated by URL prefix and naming
discipline.

### 7.5 Feature flags

Every gated capability flows through one provider:

```typescript
const canRunBacktest = useFeatureFlag('backtest.runner');
const llmModel = useFeatureFlag('strategy.llm.model', 'claude-sonnet-4');
```

Today: flags resolve from a static config file (`frontend/src/flags.ts`).
Tomorrow: flags resolve per-tenant from a flags service (LaunchDarkly,
or our own table). Same `useFeatureFlag` signature.

This is how the eventual "Pro tier unlocks the RL agent" gating works
without touching component code.

### 7.6 Quotas + cost controls

Long-running operations (backtests, screener scans over wide universes,
LLM strategy runs) flow through a quota seam:

```typescript
// frontend
const { mutate, isPending, quotaInfo } = useQuotaMutation('backtest.run', {
  // ...
});
// quotaInfo.remaining_today, quotaInfo.plan_limit, etc.
```

```python
# backend
@router.post("/api/v1/backtest")
async def run_backtest(
    config: BacktestConfig,
    principal: Principal = Depends(get_principal),
    quota: QuotaCheck = Depends(check_quota("backtest.run", cost=1)),
):
    ...
```

Today: `check_quota` always returns OK. Tomorrow: it checks the
tenant's plan, decrements counters, returns 429 with quota-info
headers when exceeded. **The cost-control machinery we already use
for the LLM agent (the SQLite cache + per-run budget cap in
[app/services/sim/strategies/llm_agent.py](../app/services/sim/strategies/llm_agent.py)
TA-2) is the same pattern, applied earlier in the stack.**

### 7.7 Observability + audit (built in from day 0)

Three subsystems, hooked to no-op providers today, real providers in
SaaS mode:

| Subsystem | Today | SaaS mode |
|---|---|---|
| **Error tracking** | Console + a `useErrorBoundary` fallback page | Sentry (or PostHog) wired to the same boundary |
| **Audit log** | `app/audit/log.py` writes structured rows to a CH `audit_events` table (already useful for dev — "what did I do yesterday?") | Same table, tenant-scoped; per-tenant audit export |
| **Usage metrics** | Same `audit_events` table feeds a `/usage` cockpit page | Per-tenant usage dashboards; basis for billing |

The audit log lands **today**, not later. Every cockpit page emits
a `view` event; every mutation emits an action event; every MCP
invocation emits a tool-call event. This is operator-debuggable value
on day 1 ("why did the screener fire 50 times yesterday?") and becomes
the SaaS audit trail without re-architecture.

### 7.8 Branding + theming

White-labeling later is purely additive:

- All color tokens, the logo SVG, and the product name live in one
  config file (`frontend/src/branding.ts`).
- Every component reads from that file, not from hardcoded strings.
- Today: hard-coded "StockAlert" / current slate-and-indigo palette.
- Future: branding swappable per-tenant or per-deployment without
  recompiling.

### 7.9 Deployment topology readiness

The current FastAPI + ClickHouse + Iceberg stack is single-process,
single-box. SaaS-grade deployment needs:

| Concern | Today's posture | Future-ready means |
|---|---|---|
| Stateless API tier | Mostly true — state lives in CH + S3 + SQLite | Move the LLM response cache out of local SQLite to CH (planned anyway for replay-across-machines reproducibility) |
| Background workers | In-process asyncio tasks | Externalize to a queue (Redis Streams or NATS); workers as separate processes. Schemas already typed. |
| WebSocket fan-out | Single process | Pub/Sub via Redis when we cross 1 instance |
| Database per-tenant isolation | Single CH cluster | Either shared schema with `owner_id` filter (planned) OR per-tenant tables (only if compliance demands it). The owner-column-everywhere discipline supports both. |

We are **not** doing any of this work now. We are flagging it so the
choices we make today (e.g. where the LLM cache lives) don't paint
us into a corner. The data-platform plan already covers most of this
(silver/gold are designed multi-tenant-friendly because Iceberg's
partition-by-something model maps cleanly to `partition by owner_id`).

### 7.10 The minimum-viable-additions for SaaS launch (one-day-rough estimate)

When the SaaS day arrives, the work to flip the switch (assuming
the seams above are in place):

| Item | Effort |
|---|---|
| Auth provider integration (Clerk / Supabase Auth / WorkOS) | 1 day |
| Backfill `owner_id` on existing tenant-scoped tables (CH migration) | 0.5 day |
| `get_principal` reads from session/JWT | 0.5 day |
| Feature-flag provider integration | 1 day |
| Stripe (or Lemon Squeezy) billing webhook handler | 2 days |
| Quota-table + decrement logic on plan-gated endpoints | 1 day |
| Public landing page on marketing subdomain (Next.js, separate repo) | 3-5 days |
| SOC2 minimums (audit log already exists, just need retention + access policies) | 2 days |
| **Total** | **~2 weeks** |

Without the seams: that same flip is a 6-8 week refactor. **The
seams cost us approximately 3% extra effort today for a 5× cost
reduction on the SaaS-flip day.**

---

## 8. Type chain — the closing of the loop (Pydantic → React)

The single most important architectural property of this plan:

```
app/services/screener/schemas.py        (Python: Pydantic)
       │
       ▼
FastAPI /openapi.json                   (runtime schema export)
       │
       │  build step:
       │    npx openapi-typescript /openapi.json -o src/api/types.gen.ts
       ▼
frontend/src/api/types.gen.ts           (TypeScript types — ALL endpoints)
       │
       ▼
import { paths, components } from './types.gen'
const client = createClient<paths>()
       │
       ▼
client.POST('/api/screener/scan', { body: spec })   // ← spec typed end-to-end
```

The same chain extends to Zod schemas for forms:

```
ScreenerSpec (Pydantic)
       │
       │  openapi-typescript → ScreenerSpec (TS interface)
       │  ts-to-zod          → screenerSpecSchema (Zod)
       ▼
react-hook-form({ resolver: zodResolver(screenerSpecSchema) })
       │
       ▼
SubmitHandler → POST /api/screener/scan → typed response
```

The frontend never declares its own version of a backend type.
Diverge from this rule and you're back to the manual-sync hell that
killed the static-HTML approach.

---

## 9. Phasing

Each phase delivers operator-visible value and leaves the app in a
shippable state. Total estimated effort: 5-7 weeks of focused work
for FE-1..FE-10 (the cockpit), plus FE-11..FE-13 (~1-2 weeks) which
land the SaaS-readiness seams alongside. The seams are **threaded
through** the cockpit phases, not deferred — see notes on each phase.

### Phase FE-1 — Foundation + Status page + seams (6–8 days)

The foundation phase lands the SaaS-readiness seams. They cost ~1
extra day here and save weeks later.

- Scaffold `frontend/` with Vite + React + TS + TanStack Router +
  TanStack Query + Tailwind + shadcn/ui base components + biome.
- Wire OpenAPI codegen (npm script + watch mode).
- WebSocket subscription manager.
- App shell: sidebar nav + topbar + global status bar.
- **`/` Status page** with all the live subsystem indicators
  (§5.1).
- **SaaS-readiness seams** (§7):
  - `useCurrentUser` hook + `DEV_USER` constant.
  - `apiClient` with `withAuth` no-op wrapper.
  - `useFeatureFlag` reading from `frontend/src/flags.ts`.
  - `useUserSetting` localStorage wrapper.
  - `useQuotaMutation` no-op wrapper.
  - `branding.ts` config file with color/logo/product-name tokens
    used everywhere (no hardcoded strings).
  - `protected` route metadata (no-op today).
- **Backend seams** (companion TA-SaaS-1 work, can be a separate PR
  or bundled):
  - `app/auth/principal.py` with `Principal` Pydantic + `DEFAULT_PRINCIPAL`.
  - `get_principal` FastAPI dependency.
  - Audit-log table in CH (`audit_events`) + middleware that writes
    one row per request.
  - Rename `/api/*` → `/api/v1/*` (one-shot; legacy redirects to
    new for the static-HTML transition).
- Production build → `app/static/dist/`; FastAPI redirect `/` →
  `/app`.
- Legacy bridge: old pages stay at `/legacy/*`.
- CI: frontend-build job; type errors fail the build.

**Gates:**
- `/` page renders all subsystem health badges live (WS-driven).
- Vite dev server proxies cleanly to FastAPI.
- Type codegen reproducible (CI re-generates and confirms no drift).
- Every `useCurrentUser()` call returns `DEV_USER`; every `useQuotaMutation`
  flows through the no-op wrapper.
- Audit log records every cockpit page view + mutation.

### Phase FE-2 — Symbol page parity (4–5 days)

- Port `symbol.html` to React + Lightweight Charts via the React
  wrapper.
- Interval picker, indicator overlays, signal markers, recent bars
  table.
- Coverage strip beneath the chart.
- Journal-trades-on-this-ticker panel.

**Gate:** every feature of `symbol.html` works on the new page.
Legacy URL remains accessible.

### Phase FE-3 — Screener page (3 days)

- Visual `ScreenerSpec` builder (§5.3).
- Live scan + candidate table with sparklines.
- Draft persistence via `useUserSetting('screener.drafts', [])` —
  the seam abstraction, not raw `localStorage`.
- Backend: `screener_specs` CH table gets the `owner_id` column on
  creation (always `"default-tenant"` today; ready for SaaS).

**Gate:** the example specs in `app/services/screener/README.md` can
be reconstructed in the UI builder and produce the same results as
the API direct.

### Phase FE-4 — Backtest page (4 days)

- Strategy picker, parameter form, run button, equity curve,
  metrics, trade log, save-to-`agent_runs`.
- Reproducibility "Replay" button.
- Mutation flows through `useQuotaMutation('backtest.run')` —
  no-op cost-check today; real one when SaaS lands.
- `agent_runs` CH table gets `owner_id` column (already designed
  this way in `app/services/sim/registry.py` — confirm; if not,
  add).

**Gate:** running the canary SMA backtest from the UI produces a
metrics row in `agent_runs` byte-identical to the CLI run.

### Phase FE-5 — Runs page (2 days)

- `/runs` table from §5.8.
- Side-by-side compare.

**Gate:** every TA-1..TA-4.2 historical run appears in the table
and can be replayed.

### Phase FE-6 — MCP Explorer (4 days)

- §5.12 in full.
- Auto-form generation from MCP tool JSON schemas.
- Recent invocations + replay.

**Gate:** every one of the 29 (and growing) MCP tools is invokable
from the UI with form-generated args.

### Phase FE-7 — Lake + Coverage (3 days)

- `/lake` + `/coverage` per §5.6, §5.7.
- Ad-hoc PyIceberg query box.

**Gate:** can spot a bronze gap visually and trigger a backfill for
it from the UI.

### Phase FE-8 — Indicators + Watchlists + Monitors + Journal parity (4–5 days)

- Polish the remaining pages.

**Gate:** static-HTML pages can be deleted; cockpit replaces all
legacy functionality.

### Phase FE-9 — Polish: command palette, shortcuts, light theme (3 days)

- §6.1, §6.2, §6.3.

**Gate:** ⌘K opens, fuzzy-matches across pages/symbols/tools, jumps
on Enter.

### Phase FE-10 — Real-time everywhere (2–3 days)

- Promote `/ws/signals` → `/ws/events` (topic-multiplexed).
- Backend WS publisher integrates with backfill progress + backtest
  progress + monitor state changes.
- Front-end query invalidation triggered by WS events.
- WS subscription auth: today the connection is unauthenticated;
  the WS handshake gets the same `get_principal` treatment as HTTP
  routes (in dev, returns `DEFAULT_PRINCIPAL`).

**Gate:** no polling on any page; everything updates from WS pushes.

### Phase FE-11 — Auth-provider integration (when SaaS launches; ~3 days)

The first "flip the seam" phase. Single concrete provider, not a
configurable abstraction (we resist over-engineering — pick a stack
and commit).

**Recommendation: Clerk** (or Supabase Auth as the OSS alternative).

- `get_principal` reads from a Clerk session JWT instead of returning
  `DEFAULT_PRINCIPAL`.
- `useCurrentUser` calls Clerk's `useUser()` hook.
- Routes marked `protected: true` redirect to `/login` when there's no session.
- Add `/login`, `/signup`, `/account` cockpit pages (Clerk's React
  components do most of the work).
- Owner backfill migration: every existing tenant-scoped CH row gets
  `owner_id = (your_user_id_from_clerk)`. One-shot DDL+DML script.
- WS handshake: validate JWT from query param or first message.

**Gate:** A fresh dev machine cannot access cockpit data without
logging in. Existing data still loads when you log in as yourself.

### Phase FE-12 — Plans + quotas + billing (when SaaS launches; ~5 days)

- `plans` CH table: `plan_id, name, price_cents, quotas (JSON)`.
- `tenant_plans` CH table: `tenant_id, plan_id, status, period_end, ...`.
- `quota_check` middleware actually checks: read tenant plan's quota
  for the named operation, decrement a CH counter, return 429 if exceeded.
- Stripe Checkout integration (`/api/v1/billing/checkout-session`,
  webhook handler at `/api/v1/billing/webhook`).
- `/billing` cockpit page: current plan, usage, upgrade button.
- Feature-flag provider integration: gate the RL agent, the LLM
  strategy, advanced screener rules behind plan tiers via
  `useFeatureFlag('feature.id')`.

**Gate:** the Stripe webhook creates a `tenant_plans` row; the cockpit
reflects the new plan within 1 minute; quota enforcement applies
immediately for the new tenant.

### Phase FE-13 — SOC2 minimums + audit retention (when SaaS launches; ~3 days)

- Audit-log retention policy (rolling 1 year per tenant; longer for
  paid tiers).
- Per-tenant audit export endpoint (`GET /api/v1/audit/export.csv`).
- Soft-delete instead of hard-delete on all tenant-scoped tables.
- Privacy-policy + ToS pages (cockpit footer links).
- Data-deletion endpoint (`DELETE /api/v1/me`) implementing
  scrubbing across all tenant tables (GDPR right-to-be-forgotten).

**Gate:** documented data flow for SOC2 light evidence (audit log,
access patterns, retention policy). Not a full SOC2 cert — just the
foundation that makes a future audit tractable.

---

## 10. Risks & open questions

### "Frontend complexity" risk

Adding a React build pipeline doubles the surface area we maintain
in this repo. Mitigations:
- Vite + biome + shadcn/ui = the modern minimal stack. No webpack,
  no eslint-plus-prettier-plus-stylelint config sprawl.
- Type codegen is hermetic — change a Pydantic schema, frontend
  build catches the break.
- Every page is a route file; we don't introduce framework patterns
  we won't use (no Redux Toolkit, no Apollo, no Next.js).

### Bundle size

Lightweight Charts + Recharts + TanStack Query + shadcn primitives
won't be small. Mitigation: code-split routes (TanStack Router
supports this natively). Initial bundle should target < 250 KB
gzipped; route bundles < 100 KB each. Monitor via `vite-bundle-visualizer`.

### Long-running operations (backtest, deep backfill)

These can take minutes. Mitigation: progress streams over the new
`/ws/events`; UI shows a progress modal that can be backgrounded;
finished jobs notify via a top-bar drawer.

### MCP tool schema variance

MCP tools have heterogeneous response shapes (some return charts,
some return tables, some return text). The MCP Explorer's
auto-renderer needs heuristics. Mitigation: per-tool "render hint"
metadata can be added to the tool decorator if the heuristics
aren't enough (lazy — only add if needed).

### Auth + multi-tenancy (the SaaS-readiness wager)

This plan bets that landing the auth/tenancy/quota/billing **seams**
in FE-1 (~1 extra day) saves a 6-8 week refactor later. The bet
fails if:
- We never go SaaS. → ~1 day overhead wasted, no harm done.
- The seams diverge from the real eventual auth provider's shape. →
  the seams are deliberately minimal (`Principal` Pydantic, `useCurrentUser`
  hook, `withQuota` decorator) so they're close to ANY provider's shape.
  Clerk / Supabase Auth / Auth0 / Authentik all expose the same
  primitives.
- The "owner_id on every tenant table" discipline slips. → enforced by
  a lint check on PR (planned for FE-1).

Track this in the journal: if we get to FE-5 without ever using the
seams (i.e. they remained no-ops the whole time), the bet is still
winning — they cost ~1 day of FE-1 effort and they're now permanent.

### When to delete the legacy HTML

Discipline: only delete when the React replacement has reached
**parity + 1** for a given page. The "+ 1" forces at least one
new capability per page so the rewrite is justified. Track per-page
in the build journal.

---

## 11. Where this fits in the overall roadmap

The frontend track is **fully independent of the silver-layer /
gold-features / EW tracks.** No frontend phase blocks or is blocked
by data-platform work. The two tracks can run in parallel without
contention.

Recommended insertion in [trading_subsystem_design.md §10](trading_subsystem_design.md):

```
Data / TA / Trading track       │   Frontend track (parallel)
                                │
TA-4.3 Screener (LANDED)        │
TA-5   Silver layer (next)      │   FE-1 Foundation + Status + seams
TA-6   TA gap-fill              │   FE-2 Symbol parity
                                │   FE-3 Screener UI
TA-7   Gold features            │   FE-4 Backtest UI
TA-8   Universe history         │   FE-5 Runs page
                                │   FE-6 MCP Explorer
EW-1   Pivots                   │   FE-7 Lake + Coverage
EW-2   Wave engine              │   FE-8 Remaining parity
                                │   FE-9 Polish
EW-3..5  Wave integrations      │   FE-10 Real-time everywhere
TA-RL  RL agent                 │
TA-Live Paper → live            │   ┌─ SaaS flip (when ready) ─┐
                                │   │ FE-11 Auth integration   │
                                │   │ FE-12 Plans/quotas/Stripe│
                                │   │ FE-13 SOC2 minimums      │
                                │   └──────────────────────────┘
```

**Recommendation:** start FE-1 in parallel with TA-5 (silver). The
Status page makes silver work observable while it's being built —
you'll watch silver populate live in the cockpit instead of
running ad-hoc CH queries.

---

## 12. Decisions needed before FE-1 starts

Three questions. Only these block FE-1; everything else gets
decided when we hit it.

### 13.1 Stack: confirm or change

Plan recommends **React + TypeScript + Vite + TanStack + shadcn/ui +
Tailwind**. Rationale in §3.

- **Confirm** → I start scaffolding FE-1.
- **Pick HTMX instead** → I rewrite §3 and §4 for a Jinja-templates +
  HTMX path. Simpler, but the cockpit ambitions (command palette,
  MCP Explorer with live form generation, optimistic updates on
  long-running backtests) are harder to deliver. Plan still works,
  ceiling is lower.
- **Pick a different React combo** (e.g. Mantine instead of
  shadcn/ui; Redux Toolkit instead of TanStack Query) → tell me
  which substitution; I update §3.

### 13.2 SaaS-readiness seams: include in FE-1, or skip?

The plan recommends including the seams (§7) in FE-1. Cost: ~1
extra day of FE-1 effort. Benefit: a future SaaS flip is ~2
weeks of integration work instead of 6-8 weeks of refactor.

- **Include them** → FE-1 lands the seams. Plan as written.
- **Skip them** → I remove §7 and treat the cockpit as
  permanently-internal. If SaaS day ever comes, expect a
  significant refactor. Faster today; more expensive later.

### 13.3 Order: parallel with silver, or after?

- **Parallel** → FE-1 starts now, runs alongside TA-5 (silver
  layer). The new Status page makes silver-build progress
  observable as it happens.
- **Sequential** → silver first, frontend after. Safer (one focus
  area at a time), slower (you don't see the cockpit until
  TA-5..TA-8 complete).

---

## 13. Decisions deferred until we hit them

These DON'T need answers before FE-1 — they get decided on the day
we actually build the feature, because the seams in FE-1 are
deliberately generic enough to fit any reasonable choice.

- **Auth provider** (Clerk / Supabase Auth / WorkOS / Auth0). The
  `Principal` Pydantic model in §7.2 has `user_id: str, tenant_id:
  str, roles: list[str], plan: str` — every mainstream provider
  produces those four fields, so we pick when we wire it up.
  Decided during FE-11 (the day SaaS launches).

- **Marketing site posture** (separate Next.js site at
  `stockalert.com` with cockpit at `app.stockalert.com`, OR no
  marketing site, OR cockpit + landing at one domain). The URL
  separation in §7.4 (`/api/v1`, `/cockpit`, `/mcp`) is path-based
  and can move to subdomains later without code changes.
  Decided during FE-11 or later.

- **MCP Explorer priority** — currently FE-6. If during FE-2 or
  FE-3 you discover MCP debuggability is the actual bottleneck,
  bump it earlier. No need to decide now.

- **Theme tokens** — slate-darks-with-indigo-accent (matching current
  static dashboard) is the default. Decided during FE-1.

- **Chart library mixing** — Lightweight Charts for OHLCV, Recharts
  for everything else. Reconsidered during FE-2 only if a unified
  library is desirable.

- **Mobile** — fully decline now, graceful degradation considered
  during FE-9.

- **State persistence scope** — UI state only (theme, panel layouts,
  recent symbols) in localStorage via `useUserSetting`. Hot query
  results stay in TanStack Query's in-memory cache.

- **Form library scope** — Zod for form validation. Whether to also
  validate WS messages at runtime: decide during FE-10.
