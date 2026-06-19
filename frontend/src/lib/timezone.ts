/**
 * Chart / table display timezone. A single global, per-user setting
 * (`chart.timezone` via useUserSetting) drives BOTH the candlestick
 * chart's time axis and the Recent Bars table, so the two can never
 * drift apart again.
 *
 * Bar timestamps arrive as UTC instants (ISO-8601 with a `Z`); this is
 * purely a *display* concern. `"local"` resolves to `undefined`, which
 * tells `Intl` to use the viewer's browser timezone — the default.
 */

export const TZ_OPTIONS = [
  { value: "local", label: "Local", zone: undefined },
  { value: "et", label: "ET", zone: "America/New_York" },
  { value: "ct", label: "CT", zone: "America/Chicago" },
  { value: "utc", label: "UTC", zone: "UTC" },
] as const;

export type TzSetting = (typeof TZ_OPTIONS)[number]["value"];

export const DEFAULT_TZ: TzSetting = "local";

/**
 * Map a setting value to an IANA timezone string for `Intl`, or
 * `undefined` for the viewer's local zone. Unknown values (e.g. a stale
 * localStorage entry) fall back to local.
 */
export function resolveZone(setting: string): string | undefined {
  return TZ_OPTIONS.find((o) => o.value === setting)?.zone;
}
