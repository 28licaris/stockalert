import type { components } from "@/api/types.gen";
import type { Principal, Role } from "./principal";

type CurrentUserDto = components["schemas"]["CurrentUserResponse"];
type LogoutDto = components["schemas"]["LogoutResponse"];
export type DashboardSession = components["schemas"]["SessionSummary"];
type SessionListDto = components["schemas"]["SessionListResponse"];
type SessionRevocationDto = components["schemas"]["SessionRevocationResponse"];

export class AuthRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
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
): Promise<Response> {
  const csrfToken = readCookie("stockalert_csrf");
  if (!csrfToken) {
    throw new AuthRequestError(
      "Your security token is missing. Refresh and try again.",
      403,
    );
  }
  return fetch(path, {
    method,
    credentials: "include",
    headers: {
      Accept: "application/json",
      "X-CSRF-Token": csrfToken,
    },
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
