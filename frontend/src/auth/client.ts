import type { components } from "@/api/types.gen";
import type { Principal, Role } from "./principal";

type CurrentUserDto = components["schemas"]["CurrentUserResponse"];
type LogoutDto = components["schemas"]["LogoutResponse"];

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

export async function endSession(): Promise<string> {
  const csrfToken = readCookie("stockalert_csrf");
  if (!csrfToken) {
    throw new AuthRequestError(
      "Your security token is missing. Refresh and try again.",
      403,
    );
  }
  const response = await fetch("/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "X-CSRF-Token": csrfToken,
    },
  });
  if (!response.ok) {
    throw new AuthRequestError(
      "We couldn't sign you out. Please try again.",
      response.status,
    );
  }
  return ((await response.json()) as LogoutDto).redirect_url;
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
