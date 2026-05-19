/**
 * Typed errors surfaced from the FastAPI `ErrorResponse` envelope.
 *
 * Backend contract (FE-CONTRACTS-1, locked):
 *   - Every non-2xx response from `/api/v1/*` is a JSON body
 *     `{ code, message, details, request_id }`.
 *   - `code` is machine-readable ('not_found', 'rate_limited', ...).
 *   - `message` is operator-readable and safe to render in UI.
 *   - `details` carries structured field errors, retry-after, etc.
 *
 * Components never `try/catch` raw `fetch()`. They use `apiClient`
 * (which throws ApiError on non-2xx) or TanStack Query hooks (whose
 * `error` field is `ApiError`).
 */

export interface ErrorEnvelope {
  code: string;
  message: string;
  details: Record<string, unknown> | null;
  request_id: string | null;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown> | null;
  readonly requestId: string | null;

  constructor(envelope: ErrorEnvelope, status: number) {
    super(envelope.message);
    this.name = "ApiError";
    this.code = envelope.code;
    this.status = status;
    this.details = envelope.details;
    this.requestId = envelope.request_id;
    // Preserve the prototype chain for `instanceof` after transpile.
    Object.setPrototypeOf(this, ApiError.prototype);
  }

  /** True for codes the cockpit can recover from with a retry. */
  get isRetryable(): boolean {
    return (
      this.code === "service_unavailable" ||
      this.code === "gateway_timeout" ||
      this.code === "rate_limited" ||
      this.status >= 500
    );
  }
}

/**
 * Tag-check on an unknown error. Use this instead of `instanceof
 * ApiError` when the value comes from a TanStack Query hook (which
 * may have wrapped the throw).
 */
export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError || (
    typeof value === "object" &&
    value !== null &&
    (value as { name?: string }).name === "ApiError"
  );
}

/**
 * Best-effort envelope extractor. Used by the fetch helper in
 * api/client.ts when an HTTP response isn't 2xx.
 *
 * Falls back to a synthetic envelope when the response body isn't
 * a valid envelope (e.g. a load-balancer 502 returning HTML).
 */
export async function readErrorEnvelope(response: Response): Promise<ErrorEnvelope> {
  try {
    const body = (await response.json()) as Partial<ErrorEnvelope>;
    if (typeof body?.code === "string" && typeof body?.message === "string") {
      return {
        code: body.code,
        message: body.message,
        details: body.details ?? null,
        request_id: body.request_id ?? null,
      };
    }
  } catch {
    // Body wasn't JSON. Fall through.
  }
  return {
    code: `http_${response.status}`,
    message: response.statusText || `HTTP ${response.status}`,
    details: null,
    request_id: null,
  };
}
