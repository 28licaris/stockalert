import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./types.gen";
import { ApiError, readErrorEnvelope } from "@/lib/errors";

/**
 * Same-origin in prod (FastAPI serves the SPA at /app and the API at /).
 * In dev, Vite's proxy (vite.config.ts) forwards /api/v1, /mcp, /ws, and
 * /openapi.json to the FastAPI process. Either way, components use
 * relative paths — no env wiring needed.
 *
 * All cockpit traffic targets `/api/v1/*` as of FE-CONTRACTS-1. Legacy
 * `/api/*` paths still 307-redirect to v1 for backward compat with the
 * static HTML pages, but new code should never use them directly.
 */
const BASE_URL = "";

/**
 * Auth seam. Today: no-op. Future SaaS: pulls a JWT from the auth
 * provider's session and attaches it to outgoing requests. Adding
 * SaaS auth = implement this middleware, no component changes.
 */
const withAuth: Middleware = {
  async onRequest({ request }) {
    // const token = await getSessionToken();  // FE-11
    // if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
};

/**
 * Telemetry seam. Today: no-op. Future: emits per-request timing
 * to the same `audit_events` CH table the backend writes to, so the
 * /usage page sees a complete picture.
 */
const withTelemetry: Middleware = {
  async onRequest({ request }) {
    return request;
  },
};

/**
 * Error-envelope translator. Converts any non-2xx response into a
 * thrown `ApiError` (typed). After this middleware, callers can
 * assume `data` is present on success and `error: ApiError` on
 * failure.
 *
 * Backend contract (FE-CONTRACTS-1):
 *   - 4xx/5xx response body is `{ code, message, details, request_id }`.
 *   - readErrorEnvelope falls back to a synthetic envelope on
 *     non-JSON bodies (e.g. an upstream proxy 502).
 */
const withErrorEnvelope: Middleware = {
  async onResponse({ response }) {
    if (response.ok) return response;
    // Clone before reading — openapi-fetch may inspect the body too.
    const envelope = await readErrorEnvelope(response.clone());
    throw new ApiError(envelope, response.status);
  },
};

export const apiClient = createClient<paths>({ baseUrl: BASE_URL });
apiClient.use(withAuth);
apiClient.use(withTelemetry);
apiClient.use(withErrorEnvelope);

export type ApiClient = typeof apiClient;
