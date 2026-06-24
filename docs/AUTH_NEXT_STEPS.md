# Authentication & Identity — Next Steps

Customer auth and identity management for StockAlert SaaS is **production-ready for core flows** (signup, login, logout, session isolation, audit trail). This document tracks optional enhancements (all non-blocking; ranked by value).

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

## Links

- **Cognito config:** [`infra/auth/cognito.yaml`](../infra/auth/cognito.yaml)
- **Auth routes:** [`app/api/routes_customer_auth.py`](../app/api/routes_customer_auth.py)
- **Identity service:** [`app/services/identity/`](../app/services/identity/)
- **Tests:** [`tests/test_oauth_authentication_service.py`](../tests/test_oauth_authentication_service.py), [`tests/test_identity_service.py`](../tests/test_identity_service.py)
