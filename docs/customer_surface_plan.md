# Customer surfaces — home dashboard + marketing landing

Status: **plan** (2026-07-05). Confirmed decisions from product review.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Build order | **Customer dashboard first**, marketing second | The dashboard is the paying customer's front door; dogfoodable now. |
| Dashboard shape | **AI-assistant-first home** + glanceable widgets | The assistant is the differentiator; widgets give at-a-glance context. |
| Marketing hosting | **Separate static site on a CDN** (S3 + CloudFront), decoupled from the app | Zero coupling to auth/customer data → minimal attack surface; cheapest + lowest-maintenance for a solo founder. |

## Part 1 — Customer home dashboard (this initiative)

**Route:** replaces the interim `/charts` landing. Non-admin customers land on
`/` (new `DashboardPage`); operators still land on System Health. Lives inside
`AppShell`, behind `AuthGuard` (authenticated customers only).

**Layout (top → bottom):**
1. **Slim market snapshot strip** — indices/sector move (reuse `useMarketBanner`).
2. **AI assistant hero** — a prominent prompt ("Ask about any ticker, your
   watchlists, or a strategy…") with 3–4 suggested prompts. Reuses the existing
   streaming assistant (`useChatStore.send` → `/cockpit/assistant/*`); submitting
   opens the chat panel with the seeded turn. This is the centerpiece.
3. **Widget grid** (glanceable, each links to its full page):
   - **Your watchlists** — pretend-position P&L / top movers (`useMyWatchlists`
     + detail). Empty state → "create a watchlist".
   - **Strategy track records** — subscribed/library strategies' live forward
     performance (new `useStrategyLeaderboard` → `/api/v1/strategies/leaderboard`).
     The honest value prop.
   - **Recent activity** — latest news/alerts relevant to the customer
     (`useNewsDigest`; signals feed later).

**Data:** all read-only, from existing hooks/endpoints + one new leaderboard
hook. No new backend tables.

**Dependency/risk:** the AI assistant needs a working Anthropic API key —
enrichment recently failed with "credit balance too low". The dashboard degrades
gracefully (the hero still shows suggested prompts + routes to chat; the chat
surfaces the provider error if credits are out).

**Phases:**
- v1: market strip + assistant hero + watchlist widget + market/news snapshot;
  wire non-admin index → dashboard. Preview + iterate.
- v2: strategy-track-record widget (leaderboard hook) + recent-alerts feed +
  polish, empty states, responsive.

## Part 2 — Public marketing landing (next initiative)

Separate static site (S3 + CloudFront) at the apex/`www`, app at `app.` / `/app`.
Sections: hero + value prop, features (alerts, backtesting, honest forward track
record, AI assistant), pricing → CTA to Cognito signup / Stripe checkout (both
already exist). No server; pure static. Deploy = push to the bucket + CloudFront
invalidation. Scoped in a follow-up.
