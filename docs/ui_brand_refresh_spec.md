# UI Brand Refresh Spec

**Date:** 2026-06-29  
**Branch:** `codex/ui-brand-refresh`  
**Status:** draft for approval, no implementation yet

## 1. Restated ask

Refresh the existing React cockpit UI so it feels more professional,
sleek, and production-ready, using the provided Unusual Whales
screenshots as inspiration for tone and polish without copying the
brand, layout, or art direction directly.

This initiative is intentionally **UI / branding first**:

- improve the visual system, shell, and perceived quality
- establish a strong product identity and logo direction
- create a reusable design language that future pages and chart work
  inherit automatically
- avoid backend contract changes and avoid major workflow redesign in
  this pass

## 2. Scope

### In scope

- global visual identity for the cockpit
- logo / wordmark direction
- color system, typography, spacing, shadows, borders, and motion
- app shell refresh:
  - sidebar
  - topbar
  - status bar
  - assistant panel chrome
- landing experience for the authenticated app home / overview route
- reskin of the first key pages so the new system feels real:
  - `StatusPage`
  - `StreamPage`
  - `WatchlistsPage`
  - core symbol/chart chrome around existing charting
- reusable UI primitives and CSS tokens needed to carry the brand

### Out of scope for this pass

- backend API or schema changes
- chart-engine replacement or advanced chart rendering
- major information architecture changes
- new product capabilities
- a public marketing website
- copying Unusual Whales branding, mascot, wording, or exact layout

## 3. Product direction

The target feeling is:

- institutional but modern
- dark, restrained, and high-confidence
- data-first rather than consumer-fintech playful
- premium through spacing, contrast, and motion rather than ornament

The reference image suggests three qualities worth borrowing:

1. **Ambient depth**
   A near-black base with subtle grid texture and focused glow zones.

2. **Crisp hierarchy**
   Very clear distinction between chrome, content surfaces, and primary
   actions.

3. **Controlled accent usage**
   Accent color is sparse and deliberate, which makes the interface
   feel premium.

## 4. Brand concept

### Working brand position

StockAlert should read less like a utility script and more like a
professional market intelligence workspace.

Working voice:

- precise
- calm
- expert
- fast
- not meme-driven

### Recommended visual identity

Use a **signal / beacon / radar** metaphor instead of an animal mascot.
That keeps the system serious, memorable, and extensible across:

- alerts
- streaming
- watchlists
- assistant
- future chart intelligence

### Logo direction

Create a simple geometric mark built from:

- a circular beacon core
- one or two directional arcs
- a subtle upward-right bias or market-grid alignment

The mark should work in:

- sidebar square
- favicon
- topbar wordmark lockup
- chart watermark

### Wordmark direction

Keep `StockAlert` as the product name for now, but render it with a
stronger typographic system:

- `Stock` in bright neutral
- `Alert` in accent or gradient-accent treatment
- optional micro-tagline: `Market Intelligence Workspace`

## 5. Visual system decisions

### 5.1 Theme

Move from the current indigo-slate terminal look to a more premium
graphite + deep-ocean palette.

Recommended dark-theme token direction:

- background base: almost-black graphite
- secondary surfaces: blue-black / carbon
- elevated cards: cool charcoal with faint blue tint
- borders: soft steel, low-contrast
- accent: electric cyan leaning slightly blue
- success/up: cooler emerald
- danger/down: controlled red, not saturated neon

### 5.2 Surface model

The interface should use four clear layers:

1. app background
2. persistent chrome
3. elevated cards / panels
4. interactive highlights and glows

Rules:

- most surfaces use soft borders, not heavy fills
- glow belongs to important moments, not every card
- cards should feel dense and professional, not oversized and airy

### 5.3 Typography

Use more distinctive typography than the current default stack.

Recommendation:

- display / headings: `Space Grotesk` or `Sora`
- UI body: `Inter` or `Geist`
- mono: keep `JetBrains Mono`

Typography goals:

- stronger hero and section titles
- compact uppercase labels for system metadata
- cleaner numeric display for prices, counts, and timestamps

### 5.4 Motion

Motion should be subtle and useful:

- soft fade / rise on major surface entry
- hover lift and border-brightening on actionable cards
- controlled glow pulse on active assistant / live indicators
- no bouncy or playful animation

Respect `prefers-reduced-motion`.

## 6. Shell redesign

### Sidebar

Current issue:

- functional, but visually flat and generic

Spec:

- make the sidebar feel like persistent market infrastructure
- stronger brand header with real logo mark
- denser category labels
- active item should use a slim accent rail + tinted surface, not only a
  filled rectangle
- improve collapsed state so the icons still feel intentional
- add subtle internal gradient / texture, not a flat fill

### Topbar

Current issue:

- reads as a default app toolbar rather than a premium trading product

Spec:

- stronger search field treatment with inset depth
- optional compact market context chip area
- assistant trigger becomes a high-signal control rather than a plain
  icon button
- refine user menu alignment, spacing, and border treatment

### Status bar

Spec:

- shift from plain footer utility strip toward a compact operations rail
- use pill-based states with clearer semantic color and typography
- preserve density; do not make it bulky

### Assistant panel

Spec:

- align visual treatment with the new shell
- stronger separation from main content
- premium empty state and header treatment
- preserve current behavior; this is styling and interaction polish

## 7. Page-level refresh

### Home / overview route

Current router sends `/` to `StatusPage`. That page should become a
true cockpit landing view rather than only a health grid.

Spec:

- add a hero-quality overview section at the top
- include a stronger branded welcome / context area
- surface key system stats in premium summary cards
- keep operational health visible below
- make this page set the tone for every later page

### Status page content

Spec:

- restyle service cards with stronger hierarchy and clearer severity
- make scheduled jobs table look production-grade and more scan-friendly
- elevate summaries into compact intelligence tiles

### Stream page

Spec:

- emphasize "live universe control room" feel
- better tab treatment
- clearer separation between search/add/import/list panels
- make active counts and live state feel operational

### Watchlists page

Spec:

- make list/detail split more premium and intentional
- elevate creation and member-management forms
- improve empty state and selected-state treatment

### Symbol page and chart chrome

Spec:

- update surrounding controls, panels, and metadata treatment
- do not replace the chart engine in this pass
- ensure the symbol workspace already looks premium so future chart work
  lands into the right frame

## 8. Reusable design primitives

The refresh should introduce or revise a small set of reusable
primitives instead of page-by-page ad hoc styling:

- app background treatment
- section header pattern
- hero / overview panel
- intelligence stat card
- elevated data panel
- active navigation treatment
- premium search input treatment
- compact badge / pill system
- empty-state pattern
- branded logo component

## 9. Implementation plan

### Phase 1: foundation

- update `branding.ts`
- add logo asset(s) and logo component
- upgrade global theme tokens in `globals.css`
- update typography loading and font stacks
- introduce background texture / glow system

### Phase 2: shell

- refresh `AppShell`, `Sidebar`, `Topbar`, `StatusBar`
- align assistant panel chrome

### Phase 3: first-party pages

- reskin `StatusPage`
- reskin `StreamPage`
- reskin `WatchlistsPage`
- refresh symbol-page chrome around the existing chart

### Phase 4: polish

- tighten hover / focus / motion behavior
- verify desktop and mobile responsiveness
- ensure visual consistency across dark and light modes if light mode
  remains enabled

## 10. Acceptance criteria

The refresh is successful when:

- the app has a recognizable brand identity, not just a generic admin
  theme
- the shell looks premium and intentional on first load
- the home / overview route establishes a strong visual tone
- the first key workflow pages feel visually consistent
- no backend contracts change
- the current charting remains functional
- future charting and advanced visualization work can layer onto this
  system without another shell redesign

## 11. Deliverables

Implementation on `codex/ui-brand-refresh` should produce:

- updated global design tokens and typography
- logo mark and wordmark assets
- branded shell refresh
- refreshed overview / status / stream / watchlists / symbol chrome
- a short follow-up note listing any deferred chart-specific visual work

## 12. Open decisions to confirm before implementation

1. Keep the product name as `StockAlert`, or use this opportunity to
   rename the product surface.
2. Keep light mode as a supported theme, or make this refresh dark-mode
   first and defer light polish.
3. Whether the overview route should remain `StatusPage` with a hero
   section, or become a new dedicated landing/dashboard route that still
   links into status.

## 13. Recommendation

Recommended path:

- keep the name `StockAlert`
- make the refresh **dark-mode first**
- keep `/` as the operational home, but evolve it into a branded
  overview page rather than adding a second dashboard concept

That gives the app a clear premium identity quickly without expanding
scope into routing or product naming work.
