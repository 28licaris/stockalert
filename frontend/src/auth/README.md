# auth/

Session, principal and access-gating for the SPA.

## The rule

**Components never read auth state directly.** They go through
`useCurrentUser()`. Keeping the shape in one place is what makes an auth change
a hook change instead of a sweep across every page.

## Layout

| File | Owns |
|---|---|
| [AuthProvider.tsx](AuthProvider.tsx) | Session context provider; wraps the app |
| [auth-context.ts](auth-context.ts) | The React context object |
| [useCurrentUser.ts](useCurrentUser.ts) | **The public hook.** Current principal + loading state |
| [principal.ts](principal.ts) | `Principal` and `Role` types — the frontend mirror of the backend principal |
| [client.ts](client.ts) | Login / logout / session calls |
| [AuthGuard.tsx](AuthGuard.tsx) | Redirects unauthenticated users to `/login` |
| [AdminOnly.tsx](AdminOnly.tsx) | Renders children only for operators; hides admin surfaces from customers |

## Access model

Operator access comes from the `ADMIN_EMAILS` allowlist or `Role.ADMIN`.
`AdminOnly` plus `adminOnly` nav flags hide System Health, Stream and
ClickHouse from customers, and `require_operator` enforces the same split
server-side — **the frontend guard is UX, not security.** Per-page policy:
[`../../../docs/product/access_control.md`](../../../docs/product/access_control.md).

Identity, tenants and sessions are implemented server-side in
`app/services/identity/`; see
[`../../../docs/product/identity_and_subscriptions.md`](../../../docs/product/identity_and_subscriptions.md).

> `principal.ts`'s header comment still describes a dev-mode hard-coded
> principal and points at `app/auth/principal.py`. Both are stale: real auth
> shipped, and the backend principal lives in
> `app/api/auth_dependencies.py` + `app/services/identity/schemas.py`.

## Local development

Auth **breaks silently** under a bare `uvicorn --reload`: `AUTH_COOKIE_SECURE`
defaults true, and browsers drop `Secure` cookies over plain `http://localhost`,
so every CSRF-protected action fails with *"Your security token is missing."*

```bash
bash scripts/dev/run_auth_api.sh
```

Full explanation:
[`../../../docs/operations/runbook.md`](../../../docs/operations/runbook.md#auth-enabled-local-development).
