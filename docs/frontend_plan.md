# Front-End Plan — The Developer's Cockpit

How we evolve the existing static HTML dashboard into a robust,
typed, component-driven single-page application that exposes
**every** capability of the platform — data, indicators, screener,
backtests, agents, MCP tools, monitoring — in one cohesive UI.

**Status:** plan only. No code written yet.

**Goal:** a developer-first cockpit. This is YOUR control surface,
not a public-facing app. Prioritize introspection, debuggability,
keyboard speed, and density over polish.

**Companion docs:**
- [trading_subsystem_design.md](trading_subsystem_design.md) — strategy
  framework backing the Backtest + Runs pages.
- [data_platform_plan.md](data_platform_plan.md) — bronze/silver/gold
  surfaces backing the Lake + Coverage pages.
- [indicator_exposure_design.md](indicator_exposure_design.md) —
  IndicatorReader behind the Indicators + Symbol pages.
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

### Non-goals (explicit)

- **No marketing/landing page.** This is internal tooling, not a SaaS
  front door.
- **No mobile-first responsive design.** Desktop is the target. Light
  responsiveness for tablet inspection; we will not optimize for phones.
- **No authentication / multi-tenancy.** Single-user developer tool;
  if it ever ships externally we add auth as a separate phase.
- **No accessibility beyond basic.** WCAG-AA color contrast + keyboard
  navigation get done; screen-reader testing does not (until users
  appear).
- **No SSR / Next.js.** API-backed, dev tool, no SEO needs. Adding
  SSR doubles operational complexity for zero benefit.

---

## 3. Stack decisions (recommendation + rationale)

After surveying the field, my recommendation:

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
- Live ingestion rate per source (bars/sec by provider).
- Bronze + silver freshness per symbol (heatmap).
- Backfill queue: in-flight jobs + ETA.
- Monitor service: started monitors, signal rate, error counts.
- Service-map mini-diagram (read from `docs/ARCHITECTURE.md` service list).
- Live "log tail" stream over WS (last 50 INFO/ERROR lines from
  the FastAPI logger).

Powered by: `/health`, `/stats`, `/api/backfill/status`,
`/monitors`, `/api/lake/last-day` (existing); + 1 new endpoint
`/api/health/services` (composite).

### 5.2 `/symbol/{ticker}` — Symbol (PARITY + extensions)

Successor to `symbol.html`. OHLCV candlestick + indicator overlay
+ signals/divergence + Iceberg-bronze coverage strip + journal
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

Powered by: existing routes; one new combined `/api/symbol/{ticker}/overview`
to reduce roundtrips on first paint.

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

### 5.13 `/settings` — Settings (NEW)

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

## 7. Type chain — the closing of the loop

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

## 8. Phasing

Each phase delivers operator-visible value and leaves the app in a
shippable state. Total estimated effort: 4-6 weeks of focused work
(but elastic — every phase ships value on its own).

### Phase FE-1 — Foundation + Status page (5–7 days)

- Scaffold `frontend/` with Vite + React + TS + TanStack Router +
  TanStack Query + Tailwind + shadcn/ui base components + biome.
- Wire OpenAPI codegen (npm script + watch mode).
- WebSocket subscription manager.
- App shell: sidebar nav + topbar + global status bar.
- **`/` Status page** with all the live subsystem indicators
  (§5.1).
- Production build → `app/static/dist/`; FastAPI redirect `/` →
  `/app`.
- Legacy bridge: old pages stay at `/legacy/*`.
- CI: frontend-build job; type errors fail the build.

**Gate:** new `/` page renders all subsystem health badges live
(WS-driven, no polling), Vite dev server proxies cleanly, type
codegen is reproducible.

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
- Draft persistence in localStorage.

**Gate:** the example specs in `app/services/screener/README.md` can
be reconstructed in the UI builder and produce the same results as
the API direct.

### Phase FE-4 — Backtest page (4 days)

- Strategy picker, parameter form, run button, equity curve,
  metrics, trade log, save-to-`agent_runs`.
- Reproducibility "Replay" button.

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

**Gate:** no polling on any page; everything updates from WS pushes.

---

## 9. Risks & open questions

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

### Authentication

No auth in the plan. If this ever moves off `localhost`, we add:
- Single-user dev mode: nothing (current).
- Hosted: GitHub OAuth or magic-link, gated at the FastAPI middleware
  level (not at the SPA — never trust the client).

Filed as future concern.

### When to delete the legacy HTML

Discipline: only delete when the React replacement has reached
**parity + 1** for a given page. The "+ 1" forces at least one
new capability per page so the rewrite is justified. Track per-page
in the build journal.

---

## 10. Decisions deferred until we hit them

1. **Theme tokens** — Bloomberg-style amber-on-black is iconic but
   doesn't auto-apply to charts. Decide during FE-1 (probably
   slate-darks-with-indigo-accent, matching current static dashboard).
2. **Chart library mixing** — Lightweight Charts for OHLCV;
   Recharts for everything else? Or unify on one? Decide during
   FE-2 (probably keep both; LC is irreplaceable for finance).
3. **Form library scope** — Zod schemas for forms only, or for
   runtime validation of WS messages too? Decide during FE-6 (when
   the MCP Explorer surfaces dynamic schemas).
4. **Mobile** — fully decline, or graceful degradation? Decide
   during FE-9 (probably graceful: pages collapse to single column,
   nav becomes a drawer, but no mobile-specific designs).
5. **State persistence scope** — only UI state in localStorage, or
   also cache hot query results? Decide during FE-3 (probably
   UI-only; TanStack Query handles query-cache freshness better
   than localStorage).

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
TA-5   Silver layer (next)      │   FE-1 Foundation + Status
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
TA-Live Paper → live            │
```

**Recommendation:** start FE-1 in parallel with TA-5 (silver). The
Status page makes silver work observable while it's being built —
you'll watch silver populate live in the cockpit instead of
running ad-hoc CH queries.

---

## 12. Decision needed from operator

This plan assumes:

1. **React + TypeScript + Vite** (over HTMX or staying with Alpine).
   Confirm or veto.
2. **shadcn/ui** (over Mantine / MUI). Confirm or veto.
3. **Run FE-1 in parallel with TA-5 (silver)**, or sequentially
   (silver first, then frontend). Sequential is safer; parallel is
   faster.
4. **MCP Explorer (FE-6) priority** — bump earlier if agent
   development is the bottleneck. Currently slotted after
   Screener/Backtest/Runs; could be moved up to FE-2 if MCP
   debuggability is the higher pain.
5. **HTMX alternative** — if the React stack feels too heavy for
   "one developer's tool," the HTMX path is real. The cockpit
   ambition (command palette, MCP explorer, optimistic updates on
   long-running operations) is harder there; the "cleaner
   dashboard" ambition is easier. Operator's call.
