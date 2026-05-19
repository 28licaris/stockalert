import { useCallback, useEffect, useRef, useState } from "react";
import { useCurrentUser } from "@/auth/useCurrentUser";

/**
 * Per-user persisted UI state. Today: localStorage scoped by userId.
 * Future SaaS: same signature, backed by /api/v1/me/prefs so
 * settings sync across devices.
 *
 * The key is automatically scoped by user: `stockalert:{userId}:{key}`.
 * That means dev-mode keys are isolated by user even though we only
 * have one user today — when SaaS lands and there are real users on
 * shared dev machines, no collision.
 */

const PREFIX = "stockalert";

function scoped(userId: string, key: string): string {
  return `${PREFIX}:${userId}:${key}`;
}

export function useUserSetting<T>(
  key: string,
  fallback: T,
): [T, (value: T | ((prev: T) => T)) => void] {
  const { userId } = useCurrentUser();
  const fullKey = scoped(userId, key);

  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;

  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(fullKey);
      return raw === null ? fallback : (JSON.parse(raw) as T);
    } catch {
      return fallback;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(fullKey, JSON.stringify(value));
    } catch {
      // Quota exceeded / private mode. Silently degrade — settings
      // just don't persist this session.
    }
  }, [fullKey, value]);

  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) =>
        typeof next === "function" ? (next as (p: T) => T)(prev) : next,
      );
    },
    [],
  );

  return [value, set];
}
