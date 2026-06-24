# Authentication & Identity — Next Steps

Customer auth and identity management for StockAlert SaaS is **production-ready for core flows** (signup, login, logout, session isolation, audit trail). This document tracks optional enhancements (all non-blocking; ranked by value).

## ⚠️ Local dev caveat: must use `scripts/dev/run_auth_api.sh`

**Do not start the auth-enabled backend with plain `poetry run uvicorn app.main_api:app --reload`.** It works for everything else (charts, lake, futures), but auth will silently break:

- `app/config.py:263` defaults `AUTH_COOKIE_SECURE=true`. Browsers refuse to set/send `Secure` cookies over plain `http://localhost` — so the session + CSRF cookies are dropped, and any CSRF-protected action (logout, session revoke, MFA enrollment, billing checkout) fails with `"Your security token is missing."`
- Cognito's `COGNITO_CLIENT_SECRET` isn't checked into `.env` (it's a live AWS secret) — without it, login exchange fails outright.

**Always run:**
```bash
bash scripts/dev/run_auth_api.sh
```
This script ([scripts/dev/run_auth_api.sh](../scripts/dev/run_auth_api.sh)) fetches the live `COGNITO_CLIENT_SECRET` via `aws cognito-idp describe-user-pool-client`, and exports `AUTH_ENABLED=true`, `AUTH_COOKIE_SECURE=false`, and the identity Postgres URL before launching uvicorn. Requires `docker compose --profile identity up -d postgres` running first (see root `CLAUDE.md` commands).

If you ever see `"Your security token is missing. Refresh and try again."` locally: kill whatever's on :8000 (`lsof -ti:8000 | xargs kill -9`), restart via the script above, then log out/in again so a properly-set cookie is issued.

## Completed (this session)

✅ Cognito integration (OAuth 2.0 customer signup/login/logout)
✅ Session management (ET trading-day windows, JWT tokens)
✅ Tenant-scoped isolation (multi-customer, role-based access)
✅ Managed account security (co-managed accounts, security context)
✅ Durable audit trail (session events, security actions, effects)
✅ PostgreSQL identity layer (users, tenants, sessions, activity logs)
✅ Comprehensive test suite (unit + integration, 15+ test files)

---

## Remaining — Optional Enhancements

### 1. MFA enforcement (medium effort, high priority)

**What:** Wire TOTP and SMS OTP into the login flow. The service stubs exist (`app/services/identity/mfa_service.py`); integration is the remaining work.

**Why:** Required for SOC 2 / compliance-driven customers. Currently optional; most users won't enable it. Wiring it is the last credential-security requirement.

**Where:**
- `app/api/routes_customer_auth.py`: extend login endpoint to check `mfa_required` flag on user → challenge.
- `mfa_service.py`: wire `verify_totp()` and `verify_sms_otp()` into the login flow.
- Cognito User Pools: enable MFA device tracking (TOTP first, SMS secondary).
- Frontend: add MFA code entry dialog post-login if challenged.

**Effort:** 2–3 hours (mostly wiring existing stubs)
**Impact:** High (compliance requirement for regulated users)
**Owner:** —

---

### 2. Provider credential management UI (medium effort, high priority)

**What:** Let users link/unlink their Schwab and Polygon credentials per account (OAuth or API key paste). Store encrypted in `provider_session` table (foundation exists).

**Why:** Users need a way to connect their own broker/data accounts without embedding credentials in .env. Currently hardcoded to a single set.

**Where:**
- `app/services/identity/provider_session.py`: extend to create/revoke provider tokens (Schwab OAuth, Polygon key).
- `app/api/routes_customer_auth.py`: add `/providers/connect` and `/providers/revoke` endpoints.
- Frontend: add a "Connected Accounts" settings page.
- Credential storage: use AWS Secrets Manager or encrypted DB column (choose one).

**Effort:** 3–4 hours (mostly UI + endpoint wiring)
**Impact:** High (enables multi-user multi-provider setup)
**Owner:** —

---

### 3. Billing integration (low effort, medium priority)

**What:** Link Stripe customer ID to Cognito user ID on signup, so billing events can be tied to accounts.

**Why:** Billing/subscription management needs to know which customer paid. Currently billing and auth are unlinked.

**Where:**
- Signup flow (`routes_customer_auth.py`): after Cognito user created, call Stripe to create a customer → store stripe_customer_id in `users` table.
- Billing service (future): use stripe_customer_id to manage subscriptions, invoices, etc.

**Effort:** 1 hour (one API call + DB field)
**Impact:** Medium (enables subscriptions)
**Owner:** —

---

### 4. Audit dashboard (low effort, low priority)

**What:** UI to view security events (login attempts, session creation, account changes, API usage) per account.

**Why:** Compliance/transparency. Users can review who accessed their account and when.

**Where:**
- `app/api/routes_security.py` (new): query `identity.session_events` + `identity.security_activity` → JSON list.
- Frontend: new page `/settings/security` with event log table (filterable by date, action, status).

**Effort:** 2–3 hours (mostly UI/query)
**Impact:** Low (nice-to-have for compliance)
**Owner:** —

---

### 5. Rate limiting (low effort, low priority)

**What:** Cap login attempts (5/min per email), API calls (1000/min per tenant), to prevent brute-force and abuse.

**Why:** Security best practice. Without it, accounts are vulnerable to credential stuffing.

**Where:**
- FastAPI middleware: check X-Forwarded-For or Cognito sub → increment counter in Redis/DB → reject if over limit.
- Or: use a third-party middleware (e.g., `slowapi`).

**Effort:** 1–2 hours (middleware setup)
**Impact:** Low (not urgent for internal testing; important for production)
**Owner:** —

---

### 6. SSO / federated login (medium effort, low priority)

**What:** Let users sign in via Google, GitHub, or Apple (instead of email/password). Cognito supports this natively.

**Why:** Convenience. Reduces password fatigue; one less set of credentials to remember.

**Where:**
- AWS Cognito console: add Google/GitHub/Apple identity providers.
- Frontend: add "Sign in with Google" button.
- No backend code needed (Cognito handles it).

**Effort:** 30 min (mostly Cognito setup + button)
**Impact:** Low (nice-to-have; email/password is sufficient)
**Owner:** —

---

## Summary

**Ship now:** auth + sessions + audit trail are production-ready.

**Recommended next (in order):**
1. #1 (MFA) for compliance.
2. #2 (provider credentials) for multi-user multi-provider.
3. #3 (billing) to enable subscriptions.
4. #4 (audit dashboard) for transparency.

**Nice-to-have:** #5 (rate limiting) and #6 (SSO) are valuable but not urgent for internal testing.

---

## How this works in production (cloud)

The local-only failure mode above doesn't exist in the cloud, for two reasons:

**1. HTTPS is real, so `AUTH_COOKIE_SECURE=true` just works.**
In prod, the FastAPI app sits behind a load balancer (ALB/CloudFront) that terminates TLS — every request the browser sees is `https://`. With `AUTH_COOKIE_SECURE=true` (the production value, and the default in [config.py:263](../app/config.py:263) for exactly this reason), the browser happily sets and returns the `Secure` session + CSRF cookies on every request. There's no plain-HTTP path for them to be silently dropped on.

**2. Secrets come from AWS, not a fetched-at-startup CLI call.**
The dev script's `aws cognito-idp describe-user-pool-client` trick is a local convenience — it assumes a developer's AWS credentials and the `stockalert-admin` profile. In production this doesn't run at all. Instead:
- `COGNITO_CLIENT_SECRET`, `IDENTITY_DATABASE_URL`, `STRIPE_SECRET_KEY`, etc. are pulled from **AWS Secrets Manager / SSM Parameter Store** and injected as container environment variables at deploy time (e.g. ECS task definition `secrets:` block, or Lambda environment encryption).
- `COGNITO_REDIRECT_URI` / `COGNITO_LOGOUT_URI` point at the real domain (`https://app.stockalert.io/auth/callback`), not `localhost:8000` — these are registered as allowed callback/logout URLs on the Cognito App Client (`infra/auth/cognito.yaml` `CallbackUrls` / `LogoutUrls` params).
- `AUTH_PROVIDER_TOKEN_CIPHER=kms` (vs. local's `=local`) — provider OAuth tokens (Schwab/Polygon) are encrypted with a customer-managed KMS key instead of a local dev key.

**Net effect:** the exact same code path (`routes_auth.py` → `OAuthAuthenticationService` → cookie `set_cookie` calls) runs in both environments — only the environment variables differ. Nothing in the auth code itself needs to know it's "in the cloud"; the cookie-secure flag and secret source are the only things that flip between dev and prod.

| | Local dev | Production |
|---|---|---|
| Transport | `http://localhost` | `https://` behind ALB/CloudFront |
| `AUTH_COOKIE_SECURE` | `false` (script-set) | `true` (config default) |
| Cognito client secret | Fetched live via AWS CLI in `run_auth_api.sh` | Injected from Secrets Manager at deploy |
| Redirect/logout URIs | `localhost:8000/...` | Real domain, registered on the Cognito App Client |
| Provider token cipher | `local` | `kms` |

## Links

- **Cognito config:** [`infra/auth/cognito.yaml`](../infra/auth/cognito.yaml)
- **Auth routes:** [`app/api/routes_customer_auth.py`](../app/api/routes_customer_auth.py)
- **Identity service:** [`app/services/identity/`](../app/services/identity/)
- **Tests:** [`test_oauth_authentication_service.py`](../app/services/identity/tests/test_oauth_authentication_service.py), [`test_identity_service.py`](../app/services/identity/tests/test_identity_service.py)
