import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { DEV_PRINCIPAL, type Principal } from "./principal";
import { endSession, fetchCurrentUser } from "./client";
import {
  AuthContext,
  type AuthContextValue,
  type AuthStatus,
} from "./auth-context";

const requestedMode = import.meta.env.VITE_AUTH_MODE;
const authMode = requestedMode ?? (import.meta.env.DEV ? "dev" : "session");
const isDevPrincipal = authMode === "dev";
const isPreview = import.meta.env.DEV && authMode === "preview";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(
    isDevPrincipal
      ? "authenticated"
      : isPreview
        ? "unauthenticated"
        : "loading",
  );
  const [user, setUser] = useState<Principal | null>(
    isDevPrincipal ? DEV_PRINCIPAL : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  const refresh = useCallback(async () => {
    if (isDevPrincipal) {
      setUser(DEV_PRINCIPAL);
      setStatus("authenticated");
      return;
    }
    if (isPreview) {
      setUser(null);
      setStatus("unauthenticated");
      return;
    }
    setStatus("loading");
    setError(null);
    try {
      const current = await fetchCurrentUser();
      setUser(current);
      setStatus(current ? "authenticated" : "unauthenticated");
    } catch (caught) {
      setUser(null);
      setStatus("error");
      setError(
        caught instanceof Error
          ? caught.message
          : "Authentication is unavailable.",
      );
    }
  }, []);

  useEffect(() => {
    if (!isDevPrincipal && !isPreview) void refresh();
  }, [refresh]);

  const signOut = useCallback(async () => {
    setSigningOut(true);
    setError(null);
    try {
      const redirectUrl = await endSession();
      window.location.assign(redirectUrl);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign out failed.");
      setSigningOut(false);
      throw caught;
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, error, signingOut, refresh, signOut }),
    [status, user, error, signingOut, refresh, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
