import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useCurrentUser } from "./useCurrentUser";

/**
 * Route guard for operator-only pages (system health, ClickHouse console).
 * Non-operators are redirected to their customer home. This mirrors the
 * server-side `require_operator` gate on the corresponding APIs — the
 * backend is the real security boundary; this just keeps non-admins from
 * landing on a page whose data they can't load.
 */
export function AdminOnly({
  children,
  redirectTo = "/charts",
}: {
  children: ReactNode;
  redirectTo?: string;
}) {
  const user = useCurrentUser();
  if (!user.permissions.includes("operator.access")) {
    return <Navigate to={redirectTo} replace />;
  }
  return <>{children}</>;
}
