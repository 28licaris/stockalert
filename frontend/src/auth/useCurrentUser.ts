import { DEV_PRINCIPAL, type Principal } from "./principal";

/**
 * The seam. Every component asking "who is this?" goes through here.
 *
 * Today: returns DEV_PRINCIPAL synchronously.
 * Future (FE-11): wraps a context populated by the auth provider's
 * hook (e.g. Clerk's `useUser`); returns null while loading; triggers
 * a redirect to /login when unauthenticated on protected routes.
 *
 * The signature stays sync-returning-Principal so today's components
 * don't need to handle loading states. When SaaS lands, a thin
 * `<AuthGuard>` at the router root will suspend until the principal
 * resolves — components downstream stay simple.
 */
export function useCurrentUser(): Principal {
  return DEV_PRINCIPAL;
}
