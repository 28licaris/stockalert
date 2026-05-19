import { isApiError } from "@/lib/errors";

interface ApiErrorAlertProps {
  error: unknown;
  className?: string;
}

/**
 * Renders the typed `ErrorResponse` envelope a user can act on.
 * Three layers of detail:
 *   1. The headline `message` — always shown.
 *   2. The `code` badge — useful for filing issues.
 *   3. The `request_id` — collapsible; pasteable into log search.
 *
 * Non-ApiError throws (generic JS errors, network failures) get a
 * generic shell with the message string so we still render something
 * actionable.
 */
export function ApiErrorAlert({ error, className }: ApiErrorAlertProps) {
  const isApi = isApiError(error);
  const message =
    error instanceof Error ? error.message : String(error ?? "Unknown error");
  const code = isApi ? error.code : "client_error";
  const requestId = isApi ? error.requestId : null;

  return (
    <div
      role="alert"
      className={
        "rounded-md border border-danger/60 bg-danger/10 px-4 py-3 text-sm text-danger " +
        (className ?? "")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{message}</span>
        <code className="rounded bg-bg-base/40 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider">
          {code}
        </code>
      </div>
      {requestId ? (
        <div className="mt-1 font-mono text-[10px] text-danger/70">
          request_id: {requestId}
        </div>
      ) : null}
    </div>
  );
}
