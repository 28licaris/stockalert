/**
 * Frontend mirror of `app/auth/principal.py` (TA-SaaS-1, separate PR).
 *
 * In dev mode: a single hard-coded principal with full access.
 * In SaaS mode: populated from the auth provider (Clerk / Supabase /
 * etc.) and refreshed when the session changes.
 *
 * Components NEVER read auth state directly — they go through
 * `useCurrentUser()`. Keeping the shape in one place means the SaaS
 * flip is "swap the hook implementation"; no component touches.
 */

export type Plan = "dev" | "free" | "pro" | "enterprise";
export type Role = "owner" | "admin" | "member" | "viewer";

export interface Principal {
  userId: string;
  tenantId: string;
  email: string | null;
  displayName: string;
  roles: Role[];
  plan: Plan;
}

export const DEV_PRINCIPAL: Principal = {
  userId: "default-user",
  tenantId: "default-tenant",
  email: null,
  displayName: "Developer",
  roles: ["owner"],
  plan: "dev",
};
