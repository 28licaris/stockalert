import type { components } from "@/api/types.gen";
import type { Principal, Role } from "./principal";

type CurrentUserDto = components["schemas"]["CurrentUserResponse"];
type LogoutDto = components["schemas"]["LogoutResponse"];
export type DashboardSession = components["schemas"]["SessionSummary"];
export type SecurityEvent = components["schemas"]["SecurityEventRecord"];
type SessionListDto = components["schemas"]["SessionListResponse"];
type SessionRevocationDto = components["schemas"]["SessionRevocationResponse"];
export type MfaStatus = components["schemas"]["MfaStatusResponse"];
export type MfaEnrollment = components["schemas"]["MfaEnrollmentResponse"];
type MfaVerificationDto = components["schemas"]["MfaVerificationResponse"];

export class AuthRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "AuthRequestError";
  }
}

function toPrincipal(dto: CurrentUserDto): Principal {
  return {
    userId: dto.user_id,
    tenantId: dto.tenant_id,
    email: dto.email,
    displayName: dto.display_name,
    roles: dto.roles as Role[],
    permissions: dto.permissions,
    entitlements: dto.entitlements,
  };
}

export async function fetchCurrentUser(
  signal?: AbortSignal,
): Promise<Principal | null> {
  const response = await fetch("/api/v1/customer/me", {
    credentials: "include",
    headers: { Accept: "application/json" },
    signal,
  });
  if (response.status === 401) return null;
  if (!response.ok) {
    throw new AuthRequestError(
      "Unable to verify your session.",
      response.status,
    );
  }
  return toPrincipal((await response.json()) as CurrentUserDto);
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const match = document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

async function authenticatedMutation(
  path: string,
  method: "POST" | "DELETE",
  body?: unknown,
): Promise<Response> {
  const csrfToken = readCookie("stockalert_csrf");
  if (!csrfToken) {
    throw new AuthRequestError(
      "Your security token is missing. Refresh and try again.",
      403,
    );
  }
  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-CSRF-Token": csrfToken,
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return fetch(path, {
    method,
    credentials: "include",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

export async function endSession(): Promise<string> {
  const response = await authenticatedMutation("/auth/logout", "POST");
  if (!response.ok) {
    throw new AuthRequestError(
      "We couldn't sign you out. Please try again.",
      response.status,
    );
  }
  return ((await response.json()) as LogoutDto).redirect_url;
}

export async function fetchSessions(): Promise<DashboardSession[]> {
  const response = await fetch("/api/v1/customer/sessions", {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new AuthRequestError(
      "We couldn't load your active sessions.",
      response.status,
    );
  }
  return ((await response.json()) as SessionListDto).sessions;
}

export async function fetchSecurityEvents(): Promise<SecurityEvent[]> {
  const response = await fetch("/api/v1/customer/security-events", {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new AuthRequestError(
      "We couldn't load security activity.",
      response.status,
    );
  }
  const payload = (await response.json()) as {
    events: SecurityEvent[];
  };
  return payload.events;
}

export async function revokeSession(sessionId: string): Promise<number> {
  const response = await authenticatedMutation(
    `/api/v1/customer/sessions/${encodeURIComponent(sessionId)}`,
    "DELETE",
  );
  if (!response.ok) {
    throw new AuthRequestError(
      "We couldn't revoke that session.",
      response.status,
    );
  }
  return ((await response.json()) as SessionRevocationDto).revoked_count;
}

export async function revokeOtherSessions(): Promise<number> {
  const response = await authenticatedMutation(
    "/api/v1/customer/sessions/revoke-others",
    "POST",
  );
  if (!response.ok) {
    throw new AuthRequestError(
      "We couldn't revoke your other sessions.",
      response.status,
    );
  }
  return ((await response.json()) as SessionRevocationDto).revoked_count;
}

async function mfaError(
  response: Response,
  fallback: string,
): Promise<AuthRequestError> {
  const code = response.headers.get("X-Error-Code") ?? undefined;
  let detail = fallback;
  try {
    const payload = (await response.json()) as { detail?: string };
    if (payload.detail) detail = payload.detail;
  } catch {
    // Non-JSON error body; keep the fallback message.
  }
  return new AuthRequestError(detail, response.status, code);
}

export async function fetchMfaStatus(): Promise<MfaStatus> {
  const response = await fetch("/api/v1/customer/mfa", {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw await mfaError(response, "We couldn't load your MFA status.");
  }
  return (await response.json()) as MfaStatus;
}

export async function beginMfaEnrollment(): Promise<MfaEnrollment> {
  const response = await authenticatedMutation(
    "/api/v1/customer/mfa/enrollment",
    "POST",
  );
  if (!response.ok) {
    throw await mfaError(response, "We couldn't start MFA enrollment.");
  }
  return (await response.json()) as MfaEnrollment;
}

export async function verifyMfaEnrollment(code: string): Promise<boolean> {
  const response = await authenticatedMutation(
    "/api/v1/customer/mfa/enrollment/verify",
    "POST",
    { code },
  );
  if (!response.ok) {
    throw await mfaError(response, "We couldn't verify that code.");
  }
  return ((await response.json()) as MfaVerificationDto).enabled;
}

export type BillingStatus = components["schemas"]["SubscriptionStatusResponse"];

export async function fetchBillingStatus(): Promise<BillingStatus> {
  const response = await fetch("/api/v1/customer/billing", {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw await mfaError(response, "We couldn't load your subscription.");
  }
  return (await response.json()) as BillingStatus;
}

export async function startCheckout(
  plan: "monthly" | "annual",
): Promise<string> {
  const response = await authenticatedMutation(
    "/api/v1/customer/billing/checkout",
    "POST",
    { plan },
  );
  if (!response.ok) {
    throw await mfaError(response, "We couldn't open checkout.");
  }
  return ((await response.json()) as { url: string }).url;
}

export async function openBillingPortal(): Promise<string> {
  const response = await authenticatedMutation(
    "/api/v1/customer/billing/portal",
    "POST",
  );
  if (!response.ok) {
    throw await mfaError(response, "We couldn't open the billing portal.");
  }
  return ((await response.json()) as { url: string }).url;
}

export function loginUrl(
  returnTo: string,
  provider?: "Google",
  mode: "login" | "signup" = "login",
): string {
  const query = new URLSearchParams({ return_to: returnTo, mode });
  if (provider) query.set("provider", provider);
  return `/auth/login?${query.toString()}`;
}

export function passwordResetUrl(): string {
  return "/auth/password-reset";
}
