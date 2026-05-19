/**
 * Feature flag seam. Today: a static map. Future: a provider call
 * (LaunchDarkly / our own table) keyed by tenant + plan.
 *
 * Naming convention: dot-separated namespaces.
 *   page.<page-name>   — page is enabled in the sidebar
 *   feature.<id>       — capability gate (often plan-tier gated later)
 *   model.<id>         — pick a default model / variant
 *
 * Components read flags via `useFeatureFlag('id', defaultValue)`.
 * Adding a new flag here is the cheapest possible kill-switch.
 */

type FlagValue = boolean | string | number;

const FLAGS: Record<string, FlagValue> = {
  // Page-level gates. False = hide from sidebar (still routable via URL
  // if you know the path — these aren't security boundaries).
  "page.status": true,
  "page.symbol": true, // enabled in FE-2
  "page.screener": false,
  "page.backtest": false,
  "page.indicators": false,
  "page.lake": false,
  "page.coverage": false,
  "page.runs": false,
  "page.journal": false,
  "page.monitors": false,
  "page.watchlists": false,
  "page.mcp": false,
  "page.settings": false,

  // Capability gates. False = no-op / hidden control.
  "feature.backtest.runner": false,
  "feature.screener.scan": false,
  "feature.mcp.invoke": false,
  "feature.command-palette": false,

  // Model picks for LLM-driven strategies (read by the backend too,
  // eventually — for now this is FE-only).
  "model.strategy.llm": "claude-sonnet-4-6",
};

export function useFeatureFlag<T extends FlagValue>(key: string, fallback: T): T {
  const value = FLAGS[key];
  if (value === undefined) return fallback;
  return value as T;
}

export function allFlags(): Readonly<Record<string, FlagValue>> {
  return FLAGS;
}
