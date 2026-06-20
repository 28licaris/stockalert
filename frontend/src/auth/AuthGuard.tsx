import { AlertTriangle, LoaderCircle, RefreshCw } from "lucide-react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { branding } from "@/branding";
import { Button } from "@/components/ui/button";
import { useAuth } from "./auth-context";

function SessionLoading() {
  return (
    <div className="grid min-h-full place-items-center bg-bg-base px-6">
      <div className="flex flex-col items-center gap-4 text-center">
        <div className="grid h-12 w-12 place-items-center rounded-2xl border border-accent/30 bg-accent/10">
          <LoaderCircle className="h-5 w-5 animate-spin text-accent" />
        </div>
        <div>
          <p className="text-sm font-medium text-fg-base">
            Securing your workspace
          </p>
          <p className="mt-1 text-xs text-fg-subtle">
            Verifying your {branding.productName} session…
          </p>
        </div>
      </div>
    </div>
  );
}

function SessionError({
  message,
  retry,
}: {
  message: string | null;
  retry: () => Promise<void>;
}) {
  return (
    <div className="grid min-h-full place-items-center bg-bg-base px-6">
      <div className="w-full max-w-md rounded-2xl border border-border bg-bg-subtle p-7 text-center shadow-2xl shadow-black/20">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-warning/10 text-warning">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <h1 className="mt-5 text-lg font-semibold text-fg-base">
          Session service unavailable
        </h1>
        <p className="mt-2 text-sm leading-6 text-fg-muted">
          {message ??
            "We couldn't verify your session. This is usually temporary."}
        </p>
        <Button className="mt-6 w-full" onClick={() => void retry()}>
          <RefreshCw className="h-4 w-4" />
          Try again
        </Button>
      </div>
    </div>
  );
}

export function AuthGuard() {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") return <SessionLoading />;
  if (auth.status === "error")
    return <SessionError message={auth.error} retry={auth.refresh} />;
  if (auth.status === "unauthenticated") {
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    return (
      <Navigate
        to={`/login?return_to=${encodeURIComponent(returnTo)}`}
        replace
      />
    );
  }
  return <Outlet />;
}
