import { Link, useLocation } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  const location = useLocation();
  return (
    <div className="mx-auto max-w-xl space-y-4 p-6 text-center">
      <div className="font-mono text-6xl text-fg-subtle">404</div>
      <h1 className="text-2xl font-semibold text-fg-base">Page not built yet</h1>
      <p className="text-sm text-fg-muted">
        No route matches{" "}
        <code className="rounded bg-bg-muted px-1.5 py-0.5 font-mono text-xs">
          {location.pathname}
        </code>
        . The cockpit is shipping page-by-page — see{" "}
        <code className="font-mono text-xs">docs/frontend_plan.md §5</code> for
        what's coming.
      </p>
      <div className="flex justify-center pt-2">
        <Button variant="outline" asChild>
          <Link to="/">
            <ArrowLeft className="h-4 w-4" />
            Back to Status
          </Link>
        </Button>
      </div>
    </div>
  );
}
