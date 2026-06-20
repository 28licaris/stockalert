import { useAuth } from "./auth-context";
import type { Principal } from "./principal";

/** Protected descendants use this synchronous seam after AuthGuard resolves. */
export function useCurrentUser(): Principal {
  const { user } = useAuth();
  if (!user) {
    throw new Error(
      "useCurrentUser must be used inside an authenticated route",
    );
  }
  return user;
}
