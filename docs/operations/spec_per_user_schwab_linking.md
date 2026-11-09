# Spec — per-user Schwab linking and a tenant-scoped journal

Status: **SPEC — approved by the operator in conversation; written 2026-11-09.**
Nothing here is built. **Blocked on Schwab:** the registration path (§2) must be
settled before the link flow (phase 2) is built; phase 1 can proceed.
Companion specs: [`spec_unstreamed_tickers.md`](spec_unstreamed_tickers.md),
[`spec_auth_token_automation.md`](spec_auth_token_automation.md).

## 0. The ask, restated

A friend links his own Schwab account and sees his own trading journal. Today
the journal (`/api/v1/journal/*`) reads only the operator's account (one
shared refresh token), is operator-only in the route policy, and stores data
in ClickHouse tables with **no tenant column**.

Product scope: read-only journal; not a broker; minimize PII
(see [`../product/identity_and_subscriptions.md`](../product/identity_and_subscriptions.md)).

## 1. Who needs a Schwab token (clears up a common confusion)

| Credential | Whose | Who renews | Customer involvement |
|---|---|---|---|
| Platform market-data token (stream, REST quotes, bars) | Operator, one for everyone | Operator, weekly, one click ([auth spec](spec_auth_token_automation.md)) | **None** — a customer signing in never touches it |
| Brokerage link (journal) | One per linking customer | That customer, weekly, via reminder + "Reconnect Schwab" | Only for customers who choose to link |

## 2. Schwab terms — what is verified and what is not

Schwab's own terms could not be read (developer.schwab.com returns 403 to
automated fetches). **Verified**, from the portal pages saved in
[`../reference/schwab-api/security_api.md`](../reference/schwab-api/security_api.md):

- "Apps may only subscribe to a single API Product, for example, Trader API -
  Individual" (line 88); the doc names both Individual and Commercial (line 310).
- Each user consents and selects which accounts to share (line 39).
- The refresh token is valid 7 days; a password reset invalidates it (line 304).

**Not verified (third-party sources only):** an app serving other people's
self-directed accounts must register under Trader API - Commercial and pass a
Schwab review before clients can link; the review looks at order-placement
control and automated/AI-driven features; onboarding can be slow
(MyLinedChart, MultiCharts, TradersPost write-ups; one dates from 2023).

**Consequence:** the current Individual registration probably cannot be used
for a friend's account, and may also not cover serving Schwab market data to
customers at all. **Before building phase 2, the operator emails
TraderAPI@schwab.com** with three questions: (1) does a friends-and-family
journal fit Individual or need Commercial; (2) may Schwab market data be
served to our customers; (3) do Commercial refresh tokens last longer than 7
days. Record the answer in this section.

### Link options

- **A. Commercial registration (target).** One app, many users, reviewed.
  Lead time uncertain; the review may scrutinise the AI assistant.
- **B. Bring-your-own Individual app per user.** Works for one or two friends;
  high friction; fit with Individual terms is a grey area — do not ship
  without Schwab's written confirmation.
- **C. CSV / statement import.** No OAuth, no live sync; possible bridge. Needs
  its own sign-off.

The design below is independent of A/B/C.

## 3. Design decisions

1. **Postgres for the per-user journal** (decision for sign-off recorded here):
   data is small per user; Postgres gives tenant-scoped rows, transactions and
   the existing `pg_dump` backups. Cost: a second journal store and a one-time
   migration of the operator's ClickHouse history. Rejected: adding `tenant_id`
   to the ClickHouse tables (key change = recreate; weaker row isolation).
2. **Envelope encryption with one KMS key per environment**; the
   `EncryptionContext` binds each ciphertext to `{tenant_id, connection_id}`,
   so a ciphertext copied to another tenant fails to decrypt. One key per
   tenant is not needed. Reuse the cipher pattern in
   `app/services/identity/provider_session.py`. The key and IAM go in
   `infra/dev/stack.yaml` — **the operator runs the change set.**
3. **Read-only scope.** We never request or use order placement. Store account
   hash + last four digits only; never full account numbers.
4. **Weekly re-link for linked customers** is a Schwab limit, accepted.
5. The operator's own Schwab account becomes the first connection (shares the
   store with the auth spec).

## 4. Data model (Alembic revision `20261109_07_brokerage_connections`, down_revision `20261106_06`)

- `brokerage_connections`: id, tenant_id, user_id, provider (`schwab`), status
  (`linked | expiring | expired | revoked | error`), `refresh_token_ciphertext`,
  `issued_at`, `refresh_expires_at`, `last_sync_at`, `last_error`, `app_ref`
  (which Schwab registration), created/revoked timestamps.
- `brokerage_accounts`: connection_id, `account_hash`, `last4`, label.
- `journal_account_snapshots`, `journal_trades`, `journal_trade_notes`: the
  current ClickHouse journal shapes (`app/db/init.py`) re-keyed by
  `(tenant_id, account_hash, …)`.
- New models subclass `IdentityBase`; `release.sh` runs `alembic upgrade head`
  before the new API starts.

## 5. Link flow

1. `POST /api/v1/my/brokerage/schwab/start` → authorize URL with a signed,
   single-use `state` (10 min, bound to the session and principal).
2. `GET /api/v1/my/brokerage/schwab/callback` → verify `state`, exchange the
   code, encrypt and store the token, list the accounts the user shared, store
   hashes + last4, write a `security_events` row.
3. Never log tokens or codes. Access tokens live in memory only; refresh is
   on demand, single-flight per connection.
4. At 7 days the connection becomes `expired`; reminder email 48 h ahead and an
   in-app "Reconnect Schwab" banner.

## 6. Sync

The worker loop iterates linked, non-expired connections (the operator's
included), with a **global rate limiter** across all users because the app
registration is shared. (A third-party source puts the limit at 120 req/min;
unverified.) Default lookback 30 days, same throttles as
`journal_sync.py`. A failing connection records `last_error` and moves to
`error`; it never blocks other connections.

## 7. Unlink and revoke

`DELETE /api/v1/my/brokerage/{id}` deletes the token ciphertext immediately,
sets `revoked`, and offers "also delete my journal data". Whether Schwab
exposes a token-revoke endpoint is **unverified**; until confirmed the UI tells
the user to also remove the app in their Schwab account settings.

## 8. Route policy

- Add `/api/v1/my/brokerage` and `/api/v1/my/journal` to `CUSTOMER_RULES` in
  `app/api/auth_middleware.py`. The tenant **always** comes from the signed-in
  `Principal`, never from a path or query parameter.
- `/api/v1/journal` stays operator-only (legacy, operator's account).
- `app/api/tests/test_route_policy.py` must stay green; update
  [`../product/access_control.md`](../product/access_control.md) in the same PR.

## 9. Phasing

1. **Foundation:** connections table, KMS cipher, Postgres journal tables,
   operator account as connection #1, ClickHouse → Postgres history migration
   (verified cross-side: row counts and a sample hash per account).
2. **Link flow + per-connection sync** — after the Schwab path in §2 is settled.
3. **UI:** link, status, reconnect, unlink, journal pages scoped to the user.

## 10. Tests

Cross-tenant isolation (user A cannot read B's trades or notes); ciphertext
replayed under another tenant fails to decrypt; `state` reuse and expiry
rejected; expiry transitions; unlink deletes the token; sync continues when one
connection errors; the migration is idempotent. Every Schwab and KMS
collaborator injected.

## 11. Open items

- Schwab's answers (§2), the Schwab app registration path (A/B/C).
- Revoke endpoint existence (§7); documented rate limit (§6).
- KMS key + IAM change set (operator).
