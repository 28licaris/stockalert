import { useCurrentUser } from "@/auth/useCurrentUser";
import { StatusPage } from "./status";
import { DashboardPage } from "./dashboard";

/**
 * The "/" index is role-aware: operators land on the ops System Health
 * overview; customers land on their AI-assistant home dashboard.
 */
export function HomeIndex() {
  const user = useCurrentUser();
  const isOperator = user.permissions.includes("operator.access");
  return isOperator ? <StatusPage /> : <DashboardPage />;
}
