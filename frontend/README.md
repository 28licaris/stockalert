# StockAlert Cockpit (`frontend/`)

The developer-grade React SPA for the StockAlert trading platform.
Single-tenant today, designed to graduate into a multi-tenant SaaS
product without a rewrite.

Companion docs:

- [docs/frontend_plan.md](../docs/frontend_plan.md) — full plan,
  page catalog, SaaS-readiness contract.

---

## Architecture at a glance

```
┌────────────────────────────────────────────────────────────────────┐
│  Browser                                                           │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │  React SPA  (frontend/src/)                              │      │
│  │  ┌──────────┐  ┌──────────────┐  ┌─────────────────┐    │      │
│  │  │  Routes  │→ │  Components  │→ │  Hooks / Seams  │    │      │
│  │  │  (R6)    │  │  (shadcn/ui) │  │  useCurrentUser │    │      │
│  │  └──────────┘  └──────────────┘  │  useFeatureFlag │    │      │
│  │       │                          │  useUserSetting │    │      │
│  │       │                          │  useQuotaMutation│   │      │
│  │       ▼                          └─────────────────┘    │      │
│  │  ┌────────────────────────────────────┐                 │      │
│  │  │  TanStack Query                    │                 │      │
│  │  │  (cache, dedup, refetch, optimistic)│                │      │
│  │  └────────────────┬───────────────────┘                 │      │
│  │                   │                                     │      │
│  │                   ▼                                     │      │
│  │  ┌──────────────────────────────┐  ┌────────────────┐   │      │
│  │  │  apiClient (openapi-fetch)   │  │  WebSocket(s)  │   │      │
│  │  │  types from /openapi.json    │  │  /ws/events    │   │      │
│  │  └──────────────────────────────┘  └────────────────┘   │      │
│  └──────────────────────────────────────────────────────────┘      │
│            │                              │                        │
└────────────┼──────────────────────────────┼────────────────────────┘
             ▼                              ▼
       HTTP /api/...                  WebSocket /ws/...
             │                              │
┌────────────┼──────────────────────────────┼────────────────────────┐
│            ▼                              ▼                        │
│  ┌─────────────────────────────────────────────────────┐           │
│  │  FastAPI  (app/main_api.py)                         │           │
│  │  Pydantic schemas → routes → readers → ClickHouse + │           │
│  │  Iceberg + Schwab/Polygon providers                 │           │
│  └─────────────────────────────────────────────────────┘           │
└────────────────────────────────────────────────────────────────────┘
```

**Three rules that keep this clean:**

1. **Components never `fetch()` directly.** They call query hooks
   (in `src/api/queries.ts`), which call `apiClient` from
   `src/api/client.ts`, which uses generated types from
   `src/api/types.gen.ts`. A Pydantic schema change → re-run
   `npm run codegen` → TypeScript compile breaks at the call site.
2. **Components never read auth, flags, or settings directly.**
   They go through the seams (`useCurrentUser`, `useFeatureFlag`,
   `useUserSetting`). When SaaS lands those seams get real
   implementations; component code is untouched.
3. **The frontend has zero imports from `app/`.** The contract for
   eventually lifting `frontend/` into its own repo. See *Lift-out
   contract* below.

**Persistent chrome around every page** (in `components/layout/AppShell.tsx`):

```
┌─────────────────────────────────────────────────────────────┐
│ Sidebar │ MarketBanner  (index / futures tape, 10s refresh) │
│         ├──────────────────────────────────────────────────┤
│         │ Topbar         (search trigger, user chip)        │
│         ├──────────────────────────────────────────────────┤
│         │                                                  │
│         │ <Outlet />     (the active route)                │
│         │                                                  │
│         ├──────────────────────────────────────────────────┤
│         │ StatusBar      (subsystem health pills)          │
└─────────┴──────────────────────────────────────────────────┘
```

The MarketBanner is `md+` only — the cockpit is desktop-first, and a
tape strip on a 375px phone would crowd the topbar instead of helping.

**Runtime flow (production):**

- FastAPI serves the SPA at `/app/` (see
  [app/main_api.py](../app/main_api.py)) — every URL under `/app/`
  returns `index.html`; React Router decides what to render.
- Same-origin means no CORS — `/api/...` and `/ws/...` work directly
  from the SPA.
- Vite's hashed asset names live under `/app/assets/*` and are
  cached by the browser indefinitely.

**Runtime flow (dev):**

- `vite` runs on `:5173`; `uvicorn` runs on `:8000`.
- Vite proxies `/api`, `/mcp`, `/openapi.json`, `/ws/*` to the
  backend, so the SPA code uses the same relative paths as in prod.

**State model:**

- **Server state** (everything that comes from FastAPI) lives in
  TanStack Query's cache. Never copy it into `useState` — derive
  from the query result.
- **UI state** (sidebar collapse, panel layouts, recently-viewed
  symbols) goes through `useUserSetting` so it persists per-user.
- **Cross-page UI state** (theme, command-palette open/close) goes
  through Zustand stores in `src/store/` (added in FE-9).
- **Component-local state** (form drafts, hover, focus) is plain
  `useState`.

---

## Quick start

```bash
# From the repo root (one-time):
cd frontend
npm install

# Start FastAPI in another terminal so the dev proxy has something to talk to:
#   (from repo root)
#   poetry run uvicorn app.main_api:app --reload --port 8000

# Then back in frontend/:
npm run dev          # http://localhost:5173/app/
```

Vite proxies `/api`, `/mcp`, `/openapi.json`, and `/ws/*` to the
FastAPI process at `http://localhost:8000`. Set
`STOCKALERT_BACKEND_URL` if your backend lives elsewhere.

### Production build

```bash
npm run build        # writes ../app/static/dist/
```

FastAPI auto-mounts the built SPA at `/app/` whenever
`app/static/dist/index.html` exists ([app/main_api.py](../app/main_api.py)).
No build present → no mount; legacy `/dashboard`, `/symbol/{ticker}`,
`/journal` keep working unchanged.

---

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server with HMR, proxied to FastAPI |
| `npm run build` | Type-check + production build to `../app/static/dist/` |
| `npm run preview` | Serve the built bundle locally |
| `npm run typecheck` | TypeScript only, no emit |
| `npm run lint` | ESLint over `src/` |
| `npm run lint:fix` | Auto-fix lint issues |
| `npm run format` | Prettier write |
| `npm run format:check` | Prettier check (CI-friendly) |
| `npm run codegen` | Regenerate `src/api/types.gen.ts` from `/openapi.json` (FastAPI must be running) |
| `npm run codegen:watch` | Same, but watches |

---

## The stack (locked 2026-05-18)

| Concern | Pick | Why |
|---|---|---|
| Framework | **React 18 + TypeScript** | Industry default; biggest ecosystem; most AI/SO answers |
| Build | **Vite 5** | Fastest dev HMR; standard for non-SSR React |
| Routing | **React Router v7** | Type-safe + ubiquitous (over TanStack Router) |
| Server state | **TanStack Query** | Caching, dedup, optimistic updates — the standard |
| Client state | **Zustand** | Lightweight; used sparingly for cross-page UI state |
| Styling | **Tailwind 3** (CSS vars under the hood) | Already used in legacy HTML; semantic tokens via `globals.css` |
| Components | **shadcn/ui** (Radix + Tailwind, copy-not-npm) | Own every file → infinite customization |
| API codegen | **openapi-typescript** + **openapi-fetch** | Pydantic → OpenAPI → TS types, hermetic |
| Forms (later) | **react-hook-form + Zod** | Wire when first form lands (FE-3 screener) |
| Lint/format | **ESLint + Prettier** | Ubiquitous (over Biome) |
| Icons | **lucide-react** | Tree-shaken open-source set |

See [docs/frontend_plan.md §3.0](../docs/frontend_plan.md) for the
rationale on the React Router and ESLint+Prettier swaps from the
original plan.

---

## Folder layout

```
frontend/
├── components.json            shadcn/ui config
├── eslint.config.js           ESLint flat config
├── index.html                 Vite entry
├── package.json
├── postcss.config.js
├── tailwind.config.ts
├── tsconfig.json              project references → app + node
├── tsconfig.app.json
├── tsconfig.node.json
├── vite.config.ts
└── src/
    ├── App.tsx                Router + Query providers
    ├── main.tsx               React root
    ├── branding.ts            ┐ SaaS seams (see below)
    ├── flags.ts               │
    ├── api/
    │   ├── client.ts          │
    │   ├── queryClient.ts     │
    │   └── types.gen.ts       Auto-generated from /openapi.json
    ├── auth/
    │   ├── principal.ts       │
    │   └── useCurrentUser.ts  │
    ├── hooks/
    │   └── useQuotaMutation.ts ┘
    ├── lib/
    │   ├── storage.ts         useUserSetting (localStorage today)
    │   └── utils.ts           cn() helper for shadcn
    ├── components/
    │   ├── layout/            AppShell, Sidebar, Topbar, StatusBar
    │   ├── market/            MarketBanner (always-visible tape strip)
    │   ├── charts/            OhlcvChart wrapper
    │   ├── tables/            BarsTable etc.
    │   ├── ui/                shadcn primitives (button, etc.)
    │   └── ApiErrorAlert.tsx  Typed error alert reading ErrorResponse
    ├── routes/
    │   ├── router.tsx         React Router config
    │   ├── status.tsx         /  (placeholder)
    │   └── not-found.tsx      404
    └── styles/
        └── globals.css        Tailwind + CSS color tokens
```

### Path alias

`@/` resolves to `src/`. Configured in:
- `tsconfig.app.json` (TypeScript)
- `vite.config.ts` (bundler)

Always import as `@/lib/utils`, never `../../lib/utils`.

---

## The SaaS-readiness seams

Today this is a single-tenant dev tool. The seams below are no-ops
that future SaaS work plugs into without touching components. See
[docs/frontend_plan.md §7](../docs/frontend_plan.md) for the full
contract.

| Seam | File | Today | Future |
|---|---|---|---|
| **Who is the user** | `src/auth/useCurrentUser.ts` | Returns `DEV_PRINCIPAL` | Reads from Clerk / Supabase / WorkOS session |
| **Auth headers** | `src/api/client.ts` (`withAuth`) | No-op middleware | Attaches `Authorization: Bearer <jwt>` |
| **Feature gates** | `src/flags.ts` (`useFeatureFlag`) | Static map | Per-tenant flags from a provider |
| **Cost controls** | `src/hooks/useQuotaMutation.ts` | Pass-through to `useMutation` | Checks plan quota, surfaces `quota.remaining` |
| **Persisted prefs** | `src/lib/storage.ts` (`useUserSetting`) | localStorage scoped by userId | Cloud-synced via `/api/v1/me/prefs` |
| **Product identity** | `src/branding.ts` | Static `"StockAlert"` | Per-tenant white-label config |

**Rule:** components NEVER reach past a seam to its current
implementation. The whole point is that a SaaS implementation can
be dropped in by editing seams only.

---

## Lift-out contract

This folder is designed to graduate into its own repo someday. The
contract:

1. **Zero imports from `app/`**. The frontend talks to the backend
   ONLY through HTTP / WebSocket / OpenAPI. There is no Python ↔ TS
   shared-code path; type sync is one-way (Python → OpenAPI → TS).
2. **`package.json` is self-contained.** No workspace references,
   no monorepo tooling. `cd frontend && npm install` works in
   isolation.
3. **Build output is the only coupling.** FastAPI looks for
   `app/static/dist/index.html` and mounts it if present. Move
   `frontend/` to a sibling repo + change one path in `vite.config.ts`
   and ship the build artifact to S3/CloudFront → no Python changes.
4. **Routing assumes basename `/app`.** When this becomes its own
   subdomain (`app.stockalert.com`), the only change is
   `vite.config.ts`'s `base` (`"/app/"` → `"/"`) and the router's
   `basename`.

---

## Adding a page

1. Create `src/routes/<page>.tsx` with a component export.
2. Add an entry to `src/components/layout/nav-items.ts` (`href`,
   `icon`, `flag`, `category`).
3. Flip the matching flag in `src/flags.ts` to `true` so it shows
   in the sidebar.
4. Add the route to `src/routes/router.tsx`.

The flag-driven sidebar means in-progress pages can ship behind a
`false` flag without polluting the nav.

---

## Adding a shadcn component

```bash
# Inside frontend/
npx shadcn@latest add dialog
```

`components.json` is pre-configured; components land in
`src/components/ui/`. The base `Button` is already wired as the
template.

---

## Troubleshooting

**`npm run codegen` 404s.** FastAPI must be running on
`http://localhost:8000` (or whatever `STOCKALERT_BACKEND_URL` points
at). Start it with `poetry run uvicorn app.main_api:app --reload`.

**`/app/` 404s after `npm run build`.** Restart FastAPI. The mount
is gated by file presence at process-start time.

**Sidebar is empty.** Every page flag defaults to `false` for unbuilt
features. Only `page.status` and `page.symbol` are `true` today.
