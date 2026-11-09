# Spec — productionize authentication and tokens

Status: **SPEC — approved by the operator in conversation; written 2026-11-09.**
Nothing here is built. Companion specs:
[`spec_unstreamed_tickers.md`](spec_unstreamed_tickers.md),
[`spec_per_user_schwab_linking.md`](spec_per_user_schwab_linking.md).

## 0. The ask, restated

Remove manual toil around the Schwab token, secrets, Stripe webhook secret and
Cognito, with alerting before anything lapses.

**What cannot be promised.** Schwab's refresh token lives 7 days and, once
expired or invalidated (e.g. a password reset), "App Authorization … must be
repeated" ([`../reference/schwab-api/security_api.md`](../reference/schwab-api/security_api.md),
lines 145, 304). A human consent step each week stays. We will **not** script
the Schwab login with stored credentials/TOTP: it is brittle, may breach
Schwab's terms, and means holding a brokerage password. The target is
therefore **one click, from any browser, once a week, prompted by an email
before expiry** — no laptop script, no hand-written SSM, no restart.

**Operator decisions:** alerts go to email only (existing SNS topic); one-click
weekly renewal is acceptable; the box callback URL is added to the Schwab app.

## 1. Today

- Token pushed from the Mac (`scripts/deploy/push_schwab_token.sh`), stored in
  SSM `/stockalert/<env>/schwab_refresh_token`, rendered into `.env`, read once
  at process start. A renewal needs a container recreate.
- Expiry warning is passive (health page), and its age ledger
  (`data/.schwab_token_seen.json`) lives in the container's ephemeral
  `/app/data`, so the age resets on every recreate.
- The stream websocket survives a dead token while REST fails silently
  (`ISSUES.md` `schwab-token-expiry-masked-by-live-stream`).
- No push alert: no `sns:Publish` in the instance role or code; `AlertsTopic`
  is used only by the health and disk alarms.
- Stripe webhook secret not loaded; dev Cognito session cipher is `local`.

## 2. Design

### 2.1 One-click renewal (operator-only)

- Operator page `GET /api/v1/admin/schwab/connect` → redirects to Schwab's
  authorize URL with a signed single-use `state` (10 min, bound to the
  operator session).
- `GET /api/v1/admin/schwab/callback` receives `code`, exchanges it, stores the
  token (§2.2), and shows the new expiry. Operator-only in the route policy.
- **Prerequisite (operator action):** register the box callback in the Schwab
  developer portal *alongside* the localhost one. The field accepts several
  HTTPS URLs separated by commas (255 chars total; `security_api.md` line 68).
  **Unknown:** whether editing the app triggers a Schwab re-review. Fallback if
  it does: keep the localhost callback and give the page a paste box.
- The callback host is `app.<ip>.sslip.io`, derived from the box IP. If the IP
  can change, the callback breaks; confirm an Elastic IP or move to a real
  domain.

### 2.2 Token store (shared with Spec 3)

- The platform token moves from an env var read at boot to an encrypted row in
  the `brokerage_connections` table defined in
  [`spec_per_user_schwab_linking.md`](spec_per_user_schwab_linking.md) §4,
  with `issued_at` and `refresh_expires_at`. The platform token is the
  operator's first connection.
- Processes read it at runtime with a short in-memory cache, so a renewal needs
  no restart. This replaces the ephemeral age ledger with a real expiry.
- Until Spec 3's table exists, an interim path is acceptable only if the
  operator signs it off: write the token to SSM from the callback (needs
  `ssm:PutParameter` on that one parameter) and re-read it at runtime.

### 2.3 Rolling-renewal probe (small, in scope)

Some third-party clients claim each refresh returns a new refresh token with a
fresh 7 days; Schwab's doc and the 2026-06-26 lapse suggest not. On every
access-token refresh the worker logs: did the response contain a
`refresh_token`, and does its **fingerprint** (first 8 hex of a SHA-256) differ
from the stored one. Never log the token. If Schwab does roll it, record the new
token and expiry and the weekly step disappears; if not, nothing is lost.

### 2.4 Expiry alerting (email)

An hourly worker job:

1. Probes **real REST auth** (cheap quote call), not the websocket.
2. Publishes CloudWatch metrics `SchwabTokenDaysLeft` and `SchwabRestAuthOk`.
3. Alarms to the existing SNS topic:
   - **warn** at 48 h left — email includes the renew link;
   - **urgent** at 12 h left;
   - **critical** immediately on REST auth failure.
4. A missing-metric alarm (treat missing data as breaching) catches the job
   itself dying.

IAM: add `cloudwatch:PutMetricData` (or `sns:Publish`) to the instance role in
`infra/dev/stack.yaml`. **CloudFormation change sets are run by the operator.**

### 2.5 Stripe, Cognito, secrets

- **Stripe:** the operator creates the webhook endpoint once in the Stripe
  dashboard and stores its secret with
  `printf '%s' '<secret>' | scripts/deploy/put_param.sh dev stripe_webhook_secret`,
  then releases. Add a startup/health warning when billing is enabled and the
  secret is missing.
- **Cognito:** switch `AUTH_PROVIDER_TOKEN_CIPHER` from `local` to `kms` using
  the KMS key created for Spec 3 (one key serves both).
- **Secrets inventory:** health page lists each SSM parameter with its
  last-modified age. No automatic rotation of Postgres/ClickHouse/Cognito
  secrets (rare, risky to automate); a rotation runbook goes in
  [`runbook.md`](runbook.md).

## 3. Failure behaviour

If the weekly renewal lapses, **live data stops for every customer**; the lake
and daily data keep serving. The freshness states in
[`spec_unstreamed_tickers.md`](spec_unstreamed_tickers.md) §4 show "delayed" /
"unavailable" instead of blank charts, and §2.4 alerts the operator first.

## 4. Order of work

1. Token store + runtime read (shares the Spec 3 foundation).
2. Callback + connect page + rolling-renewal probe.
3. Alerting job + IAM change set (operator runs it).
4. Stripe warning, Cognito KMS cipher, secrets inventory, runbook.

## 5. Tests

State transitions (days-left thresholds), REST-probe failure → critical,
callback `state` single-use and expiry, token never appears in logs, renewal
without restart, alert job death detected. All Schwab/AWS collaborators injected.

## 6. Open items

- Does editing the Schwab app's callback trigger re-review? (portal / TraderAPI@schwab.com)
- Elastic IP or real domain for the callback host.
- Do Commercial-tier refresh tokens last longer than 7 days? (ask Schwab)
