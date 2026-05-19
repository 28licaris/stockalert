import { useQuery } from "@tanstack/react-query";
import { ApiError, readErrorEnvelope } from "@/lib/errors";
import { apiClient } from "./client";
import type { components } from "./types.gen";

/**
 * Centralized TanStack Query hooks. Components NEVER call apiClient
 * or fetch directly — they go through hooks here so:
 *   - query keys are consistent (cache hits across pages)
 *   - refetch / stale tuning is in one place
 *   - response types come from `types.gen.ts` (FE-CONTRACTS-2+);
 *     hand-rolled interfaces have been deleted
 *
 * URL convention: `/api/v1/*` everywhere as of FE-CONTRACTS-1.
 * Errors propagate as typed `ApiError` (thrown by `apiClient`'s
 * `withErrorEnvelope` middleware) so TanStack Query surfaces them
 * as `query.error: ApiError`.
 */

// ─────────────────────────────────────────────────────────────────────
// Typed re-exports of generated schemas, so component imports stay
// `@/api/queries` (one stable location) rather than the bulky
// `components["schemas"]["Foo"]` syntax everywhere.
// ─────────────────────────────────────────────────────────────────────

export type Bar = components["schemas"]["Bar"];
export type Signal = components["schemas"]["Signal"];
export type InstrumentMatch = components["schemas"]["InstrumentMatch"];
export type InstrumentSearchResponse =
  components["schemas"]["InstrumentSearchResponse"];
export type BannerItem = components["schemas"]["BannerItem"];
export type MarketBannerResponse =
  components["schemas"]["MarketBannerResponse"];
export type Mover = components["schemas"]["Mover"];
export type MoversResponse = components["schemas"]["MoversResponse"];

// ─────────────────────────────────────────────────────────────────────
// /api/health/services — composite Status page health
// (response_model arriving in a later sub-phase; for now we keep the
// hand-rolled types because the existing FE-1.5 endpoint still uses
// the simple shape via fetch.)
// ─────────────────────────────────────────────────────────────────────

export type HealthState = "ok" | "warn" | "error" | "unknown";

export interface ServiceHealth {
  name: string;
  state: HealthState;
  detail: string;
  latency_ms: number | null;
}

export interface BackfillQueueSummary {
  queued: number;
  in_flight: number;
  completed_recent: number;
}

export interface MonitorSummary {
  started: number;
  errors: number;
}

export interface HealthServicesResponse {
  server_time: string;
  services: ServiceHealth[];
  backfill: BackfillQueueSummary;
  monitors: MonitorSummary;
}

export const queryKeys = {
  healthServices: ["health", "services"] as const,
  marketBanner: (symbols: string | undefined) =>
    ["market", "banner", symbols ?? "default"] as const,
  symbolBars: (symbol: string, interval: string, limit: number) =>
    ["symbol", "bars", symbol, interval, limit] as const,
  symbolSignals: (symbol: string, limit: number) =>
    ["symbol", "signals", symbol, limit] as const,
} as const;

/** Small fetch helper for routes that haven't yet been typed via apiClient. */
async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const envelope = await readErrorEnvelope(res.clone());
    throw new ApiError(envelope, res.status);
  }
  return (await res.json()) as T;
}

export function useHealthServices() {
  return useQuery({
    queryKey: queryKeys.healthServices,
    queryFn: () =>
      fetchJson<HealthServicesResponse>("/api/v1/health/services"),
    refetchInterval: 10_000, // 10s — health changes fast enough to feel live
    refetchOnWindowFocus: true,
    staleTime: 5_000,
  });
}

// ─────────────────────────────────────────────────────────────────────
// /api/v1/market/banner — index + futures tape (always-visible strip)
// ─────────────────────────────────────────────────────────────────────

/**
 * Index/futures tape strip. Passing `symbols=undefined` lets the
 * backend pick from `settings.market_banner_symbols`. Refresh cadence
 * matches the legacy dashboard (10s) so the AppShell strip feels
 * live without thrashing Schwab's quote API.
 */
export function useMarketBanner(symbols?: string | undefined) {
  return useQuery({
    queryKey: queryKeys.marketBanner(symbols),
    queryFn: async (): Promise<MarketBannerResponse> => {
      const params = symbols ? { query: { symbols } } : undefined;
      const { data } = await apiClient.GET("/api/v1/market/banner", {
        params: params as { query: { symbols: string } },
      });
      // apiClient throws ApiError on non-2xx; data is non-null here.
      return data as MarketBannerResponse;
    },
    refetchInterval: 10_000,
    refetchOnWindowFocus: true,
    staleTime: 5_000,
  });
}

// ─────────────────────────────────────────────────────────────────────
// /api/v1/bars — OHLCV bars for charting (FE-2; FE-CONTRACTS-2 typed)
// ─────────────────────────────────────────────────────────────────────

export function useSymbolBars(
  symbol: string | undefined,
  interval: string,
  limit = 500,
) {
  return useQuery({
    queryKey: queryKeys.symbolBars(symbol ?? "", interval, limit),
    queryFn: async (): Promise<Bar[]> => {
      if (!symbol) throw new Error("symbol required");
      // apiClient.GET goes through `withErrorEnvelope` middleware:
      // non-2xx throws ApiError, so `data` is non-null on success.
      const { data } = await apiClient.GET("/api/v1/bars", {
        params: { query: { symbol, interval, limit } },
      });
      return data ?? [];
    },
    enabled: Boolean(symbol),
    staleTime: 15_000,
  });
}

// ─────────────────────────────────────────────────────────────────────
// /api/v1/signals — divergence signals (FE-2; FE-CONTRACTS-2 typed)
// ─────────────────────────────────────────────────────────────────────

export function useSymbolSignals(symbol: string | undefined, limit = 100) {
  return useQuery({
    queryKey: queryKeys.symbolSignals(symbol ?? "", limit),
    queryFn: async (): Promise<Signal[]> => {
      if (!symbol) throw new Error("symbol required");
      const { data } = await apiClient.GET("/api/v1/signals", {
        params: { query: { symbol, limit } },
      });
      return data ?? [];
    },
    enabled: Boolean(symbol),
    staleTime: 15_000,
  });
}

// ─────────────────────────────────────────────────────────────────────
// Derivation helpers for the Signal shape.
//
// The backend Signal carries `type` (e.g. "regular_bullish_divergence")
// but not an explicit `direction` field — bull/bear is implicit in the
// type name. Chart markers + future filters branch on this, so we
// centralize the derivation here.
// ─────────────────────────────────────────────────────────────────────

export function signalDirection(signal: Signal): "bull" | "bear" | "unknown" {
  const t = (signal.type ?? "").toLowerCase();
  if (t.includes("bull")) return "bull";
  if (t.includes("bear")) return "bear";
  return "unknown";
}
