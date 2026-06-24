import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
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

// Indicators (chart overlays + oscillator panes)
export type IndicatorSeries = components["schemas"]["IndicatorSeries"];
export type IndicatorChartData = components["schemas"]["IndicatorChartData"];

export type InstrumentMatch = components["schemas"]["InstrumentMatch"];
export type InstrumentSearchResponse =
  components["schemas"]["InstrumentSearchResponse"];
export type BannerItem = components["schemas"]["BannerItem"];
export type MarketBannerResponse =
  components["schemas"]["MarketBannerResponse"];
export type Mover = components["schemas"]["Mover"];
export type MoversResponse = components["schemas"]["MoversResponse"];

// Watchlists + monitors (FE-CONTRACTS-3)
export type Watchlist = components["schemas"]["Watchlist"];
export type CreateWatchlistRequest =
  components["schemas"]["CreateWatchlistRequest"];
export type WatchlistMembersMutationResponse =
  components["schemas"]["WatchlistMembersMutationResponse"];
export type WatchlistStatus = components["schemas"]["WatchlistStatus"];
export type MonitorInfo = components["schemas"]["MonitorInfo"];

// Instruments lookup (batch — for enriching member lists)
export type InstrumentLookupResponse =
  components["schemas"]["InstrumentLookupResponse"];

// ClickHouse query (FE-CONTRACTS-6a)
export type CHColumn = components["schemas"]["CHColumn"];
export type CHTable = components["schemas"]["CHTable"];
export type ClickHouseSchemaResponse =
  components["schemas"]["ClickHouseSchemaResponse"];
export type ClickHouseQueryRequest =
  components["schemas"]["ClickHouseQueryRequest"];
export type ClickHouseQueryResponse =
  components["schemas"]["ClickHouseQueryResponse"];

// Seed universe (FE-CONTRACTS-4)
export type SeedEntry = components["schemas"]["SeedEntry"];
export type SeedUniverseResponse =
  components["schemas"]["SeedUniverseResponse"];
export type SeedMutationResponse =
  components["schemas"]["SeedMutationResponse"];
export type AddSeedRequest = components["schemas"]["AddSeedRequest"];
export type ImportSeedRequest = components["schemas"]["ImportSeedRequest"];

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

export interface StreamSummary {
  started: boolean;
  provider: string;
  provider_ready: boolean;
  provider_error: string | null;
  streaming_count: number;
  universe_count: number;
}

export interface HealthServicesResponse {
  server_time: string;
  services: ServiceHealth[];
  backfill: BackfillQueueSummary;
  monitors: MonitorSummary;
  /**
   * Live Schwab subscription state (FE-CONTRACTS-4 finalisation).
   * Optional on the wire so old API responses don't break the cockpit.
   */
  stream?: StreamSummary;
}

export const queryKeys = {
  healthServices: ["health", "services"] as const,
  marketBanner: (symbols: string | undefined) =>
    ["market", "banner", symbols ?? "default"] as const,
  symbolBars: (symbol: string, interval: string, limit: number) =>
    ["symbol", "bars", symbol, interval, limit] as const,
  lakeBars: (symbol: string, interval: string, windowDays: number) =>
    ["lake", "bars", symbol, interval, windowDays] as const,
  symbolSignals: (symbol: string, limit: number) =>
    ["symbol", "signals", symbol, limit] as const,
  indicators: (symbol: string, interval: string, ids: string) =>
    ["symbol", "indicators", symbol, interval, ids] as const,
  watchlists: ["watchlists"] as const,
  watchlist: (name: string) => ["watchlist", name] as const,
  seed: ["seed"] as const,
  instrumentSearch: (query: string, limit: number) =>
    ["instruments", "search", query, limit] as const,
  instrumentLookup: (symbols: string) =>
    ["instruments", "lookup", symbols] as const,
  clickhouseSchema: ["clickhouse", "schema"] as const,
  jobs: ["jobs"] as const,
} as const;

// ─────────────────────────────────────────────────────────────────────
// /api/v1/jobs — scheduled job registry
// ─────────────────────────────────────────────────────────────────────

export type JobStatus = "idle" | "running" | "ok" | "error" | "unknown";

export interface JobMetadata {
  name: string;
  display_name: string;
  schedule: string;
  setting_key: string | null;
  runnable: boolean;
  last_success: string | null;
  last_run_at: string | null;
  last_status: JobStatus;
  last_error: string | null;
  running: boolean;
}

export interface JobListing {
  jobs: JobMetadata[];
}

export interface JobRunResult {
  job: string;
  status: "started" | "already_running" | "not_found" | "not_runnable";
  started_at: string | null;
  detail: string | null;
}

export function useJobs() {
  return useQuery({
    queryKey: queryKeys.jobs,
    queryFn: () => fetchJson<JobListing>("/api/v1/jobs"),
    refetchInterval: 10_000,
    refetchOnWindowFocus: true,
    staleTime: 5_000,
  });
}

export function useRunJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (name: string): Promise<JobRunResult> => {
      const res = await fetch(
        `/api/v1/jobs/${encodeURIComponent(name)}/run`,
        { method: "POST" },
      );
      if (!res.ok) {
        const envelope = await readErrorEnvelope(res.clone());
        throw new ApiError(envelope, res.status);
      }
      return (await res.json()) as JobRunResult;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

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

export interface LatestPrice {
  symbol: string;
  last: number | null;
  ts: string | null;
}

/**
 * Latest streamed close per symbol straight from ClickHouse — a single fast
 * query (~tens of ms) with **no live-provider call**. Use this for a "last"
 * column over many symbols (e.g. the stream universe), where the market-banner
 * (live Schwab quotes) would be slow and rate-limit-prone at scale.
 *
 * Not in the openapi codegen, so this is a raw `fetch` (same pattern as the
 * jobs endpoint). `symbolsCsv` is a comma-separated list.
 */
export function useLatestBars(symbolsCsv: string | undefined) {
  return useQuery({
    queryKey: ["bars", "latest", symbolsCsv ?? ""] as const,
    queryFn: async (): Promise<LatestPrice[]> => {
      if (!symbolsCsv) return [];
      const res = await fetch(
        `/api/v1/bars/latest?symbols=${encodeURIComponent(symbolsCsv)}`,
      );
      if (!res.ok) {
        const envelope = await readErrorEnvelope(res.clone());
        throw new ApiError(envelope, res.status);
      }
      const data = (await res.json()) as { items: LatestPrice[] };
      return data.items ?? [];
    },
    enabled: Boolean(symbolsCsv),
    refetchInterval: 10_000,
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
// useLakeBars — CH-first chart hook with on-demand lake fill.
//
// Calls `/api/v1/bars?symbol=…&interval=…&lookback_days=N` which:
//   1. Reads from ClickHouse (sub-100ms on warm symbols)
//   2. If CH coverage is insufficient, the route reads the same
//      bounded window from `equities.polygon_adjusted` and inserts it
//      into CH, then re-queries — so the next chart load for that
//      symbol is also hot. Lake stays the cold backup; CH is the
//      canonical chart source.
//
// Aggregation (5m, 15m, …) is done server-side by ClickHouse via
// `toStartOfInterval` — no client-side resampling.
// ─────────────────────────────────────────────────────────────────────

/** How many days of history to request per display interval. */
const LAKE_WINDOW_DAYS: Record<string, number> = {
  "1m":  7,
  "5m":  30,
  "15m": 60,
  "30m": 90,
  "1h":  180,
  "1d":  365,
};

/**
 * Auto-refresh cadence per interval. Bars resample from live `ohlcv_1m`, so
 * polling re-pulls the latest closed bar AND the still-forming current bar —
 * the chart updates without a manual page refresh. Cadence is finer for
 * fast intervals; React Query pauses polling while the tab is hidden
 * (refetchIntervalInBackground defaults to false), so this is cheap.
 */
const REFETCH_MS: Record<string, number> = {
  "1m":  15_000,
  "5m":  15_000,
  "15m": 30_000,
  "30m": 30_000,
  "1h":  60_000,
  "1d":  60_000,
};

/**
 * Fetch chart bars via `/api/v1/bars` (ClickHouse). When CH doesn't
 * cover the requested window, the backend transparently fills from
 * `equities.polygon_adjusted` and re-queries — the caller never sees
 * the lake directly; data always arrives as a server-aggregated
 * `Bar[]`.
 *
 * The name `useLakeBars` is preserved for backward compatibility (the
 * symbol page + watchlists import it). The implementation now routes
 * through CH so the chart hits the hot path on every subsequent load.
 */
export function useLakeBars(symbol: string | undefined, interval: string) {
  const windowDays = LAKE_WINDOW_DAYS[interval] ?? 30;

  return useQuery({
    queryKey: queryKeys.lakeBars(symbol ?? "", interval, windowDays),
    queryFn: async (): Promise<Bar[]> => {
      if (!symbol) throw new Error("symbol required");
      const { data } = await apiClient.GET("/api/v1/bars", {
        params: {
          query: {
            symbol,
            interval,
            lookback_days: windowDays,
          },
        },
      });
      return data ?? [];
    },
    enabled: Boolean(symbol),
    // Keep data fresh enough that a window-focus or poll actually refetches.
    staleTime: 10_000,
    refetchInterval: REFETCH_MS[interval] ?? 30_000,
    refetchOnWindowFocus: true,
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
// /api/v1/indicators/chart-data — overlay + oscillator series for the
// chart. We reuse the SAME window-days + refetch cadence as `useLakeBars`
// so the indicator series stay aligned with the candles they annotate
// and refresh in lockstep. We send `params: {}` per spec — defaults-only
// first pass — and read only `series` (bars come from `useLakeBars`).
// ─────────────────────────────────────────────────────────────────────

export function useIndicators(
  symbol: string | undefined,
  interval: string,
  indicatorIds: ReadonlyArray<string>,
) {
  const windowDays = LAKE_WINDOW_DAYS[interval] ?? 30;
  // Stable, order-independent key so re-ordering selections doesn't refetch.
  const ids = [...indicatorIds].sort();
  const idsKey = ids.join(",");

  return useQuery({
    queryKey: queryKeys.indicators(symbol ?? "", interval, idsKey),
    queryFn: async (): Promise<IndicatorSeries[]> => {
      if (!symbol) throw new Error("symbol required");
      const end = new Date();
      const start = new Date(end.getTime() - windowDays * 24 * 60 * 60 * 1000);
      const { data } = await apiClient.POST("/api/v1/indicators/chart-data", {
        body: {
          symbol,
          start: start.toISOString(),
          end: end.toISOString(),
          interval,
          provider: "polygon",
          indicators: ids.map((name) => ({ name, params: {} })),
        },
      });
      return data?.series ?? [];
    },
    enabled: Boolean(symbol) && ids.length > 0,
    staleTime: 10_000,
    refetchInterval: REFETCH_MS[interval] ?? 30_000,
    refetchOnWindowFocus: true,
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

// ─────────────────────────────────────────────────────────────────────
// /api/v1/watchlists — CRUD on named watchlists (FE-CONTRACTS-3)
// ─────────────────────────────────────────────────────────────────────

export function useWatchlists() {
  return useQuery({
    queryKey: queryKeys.watchlists,
    queryFn: async (): Promise<Watchlist[]> => {
      const { data } = await apiClient.GET("/api/v1/watchlists", {
        params: { query: { include_inactive: false, with_members: true } },
      });
      return data ?? [];
    },
    staleTime: 5_000,
  });
}

/**
 * Mutation hooks invalidate the watchlist cache so any open
 * Watchlists page picks up the change immediately. The pattern keeps
 * components stateless — they just call `mutate({...})` and TanStack
 * Query handles the rest.
 */

export function useCreateWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: CreateWatchlistRequest): Promise<Watchlist> => {
      const { data } = await apiClient.POST("/api/v1/watchlists", {
        body: req,
      });
      return data as Watchlist;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.watchlists });
    },
  });
}

export function useDeleteWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (name: string): Promise<{ deleted: string }> => {
      const { data } = await apiClient.DELETE("/api/v1/watchlists/{name}", {
        params: { path: { name } },
      });
      return data as { deleted: string };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.watchlists });
    },
  });
}

export interface MutateMembersInput {
  name: string;
  symbols: string[];
}

export function useAddWatchlistMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: MutateMembersInput,
    ): Promise<WatchlistMembersMutationResponse> => {
      const { data } = await apiClient.POST(
        "/api/v1/watchlists/{name}/members",
        {
          params: { path: { name: input.name } },
          body: { symbols: input.symbols },
        },
      );
      return data as WatchlistMembersMutationResponse;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.watchlists });
    },
  });
}

export function useRemoveWatchlistMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: MutateMembersInput,
    ): Promise<WatchlistMembersMutationResponse> => {
      const { data } = await apiClient.DELETE(
        "/api/v1/watchlists/{name}/members",
        {
          params: { path: { name: input.name } },
          body: { symbols: input.symbols },
        },
      );
      return data as WatchlistMembersMutationResponse;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.watchlists });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────
// /api/v1/seed — Seed universe (FE-CONTRACTS-4)
//
// Mutations invalidate `queryKeys.seed` AND `queryKeys.watchlists`
// because seed add/remove cascades into the default watchlist via the
// refcounted subscribe machinery — any open Watchlists page should
// pick the change up too.
// ─────────────────────────────────────────────────────────────────────

export function useSeedUniverse() {
  return useQuery({
    queryKey: queryKeys.seed,
    queryFn: async (): Promise<SeedUniverseResponse> => {
      const { data } = await apiClient.GET("/api/v1/seed");
      return data as SeedUniverseResponse;
    },
    staleTime: 10_000,
  });
}

function _invalidateSeedAndWatchlists(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: queryKeys.seed });
  qc.invalidateQueries({ queryKey: queryKeys.watchlists });
}

export function useAddSeed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: AddSeedRequest): Promise<SeedMutationResponse> => {
      const { data } = await apiClient.POST("/api/v1/seed", { body: req });
      return data as SeedMutationResponse;
    },
    onSuccess: () => _invalidateSeedAndWatchlists(qc),
  });
}

export function useRemoveSeed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string): Promise<SeedMutationResponse> => {
      const { data } = await apiClient.DELETE("/api/v1/seed/{symbol}", {
        params: { path: { symbol } },
      });
      return data as SeedMutationResponse;
    },
    onSuccess: () => _invalidateSeedAndWatchlists(qc),
  });
}

export function useImportSeed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      req: ImportSeedRequest,
    ): Promise<SeedMutationResponse> => {
      const { data } = await apiClient.POST("/api/v1/seed/import", {
        body: req,
      });
      return data as SeedMutationResponse;
    },
    onSuccess: () => _invalidateSeedAndWatchlists(qc),
  });
}

// ─────────────────────────────────────────────────────────────────────
// /api/v1/stream/futures — the continuous futures roots we stream
// (separate CH table from the equities stream universe). Read-only on the
// cockpit; bootstraps from FUTURES_SEED_ROOTS on first read. Raw fetch
// (not in the openapi codegen), same pattern as useLatestBars / useJobs.
// ─────────────────────────────────────────────────────────────────────

export interface FuturesUniverseEntry {
  symbol: string;
  asset_type: string;
  added_at: string;
  added_by: string;
  notes: string;
  description: string;
}

export interface FuturesUniverseResponse {
  items: FuturesUniverseEntry[];
  count: number;
  bootstrapped: boolean;
}

const futuresUniverseKey = ["stream", "futures"] as const;

export function useFuturesUniverse() {
  return useQuery({
    queryKey: futuresUniverseKey,
    queryFn: () =>
      fetchJson<FuturesUniverseResponse>("/api/v1/stream/futures"),
    staleTime: 30_000,
  });
}

export interface FuturesCatalogEntry {
  symbol: string;
  description: string;
}

/**
 * Static catalog of known continuous futures roots (symbol + human name) —
 * backs the "add futures" autocomplete. Unlike Schwab's instrument search
 * (which returns dated contracts like /ESH27), this is the continuous roots
 * we actually stream. Cached indefinitely; it's a constant.
 */
export function useFuturesCatalog() {
  return useQuery({
    queryKey: ["stream", "futures", "catalog"] as const,
    queryFn: () =>
      fetchJson<{ items: FuturesCatalogEntry[] }>(
        "/api/v1/stream/futures/catalog",
      ),
    staleTime: Infinity,
  });
}

export function useAddFutures() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string): Promise<unknown> => {
      const res = await fetch("/api/v1/stream/futures", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol }),
      });
      if (!res.ok) {
        const envelope = await readErrorEnvelope(res.clone());
        throw new ApiError(envelope, res.status);
      }
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: futuresUniverseKey }),
  });
}

export function useRemoveFutures() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string): Promise<unknown> => {
      const res = await fetch(
        `/api/v1/stream/futures?symbol=${encodeURIComponent(symbol)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const envelope = await readErrorEnvelope(res.clone());
        throw new ApiError(envelope, res.status);
      }
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: futuresUniverseKey }),
  });
}

// ─────────────────────────────────────────────────────────────────────
// /api/v1/instruments — autocomplete + batch lookup
// ─────────────────────────────────────────────────────────────────────

/**
 * Prefix search for the SymbolSearchInput combobox. Disabled when
 * `query` is empty so the dropdown stays quiet on focus-without-type.
 * Server-side cache absorbs the keystroke burst.
 */
export function useInstrumentSearch(query: string, limit: number = 10) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: queryKeys.instrumentSearch(trimmed.toLowerCase(), limit),
    queryFn: async (): Promise<InstrumentSearchResponse> => {
      const { data } = await apiClient.GET("/api/v1/instruments/search", {
        params: { query: { q: trimmed, limit } },
      });
      return data as InstrumentSearchResponse;
    },
    enabled: trimmed.length >= 1,
    staleTime: 60_000,
    // No refetch on focus / mount — autocomplete results are stable
    // across the debounce window.
    refetchOnWindowFocus: false,
  });
}

/**
 * Batch metadata lookup for an already-known list of symbols. Returns
 * a stable `symbol → InstrumentMatch` map the caller renders against.
 *
 * Called by member lists (watchlist, seed) to enrich each row with
 * the company description in ONE round-trip instead of N. The query
 * key is the comma-joined symbol list, so callers that pass the same
 * list dedupe naturally.
 */
export function useInstrumentLookup(symbols: ReadonlyArray<string>) {
  // Stable key: dedupe + sort so {AAPL, NVDA} and {NVDA, AAPL} share cache.
  const key = useMemoSortedJoined(symbols);

  return useQuery({
    queryKey: queryKeys.instrumentLookup(key),
    queryFn: async (): Promise<InstrumentLookupResponse> => {
      const { data } = await apiClient.GET("/api/v1/instruments/lookup", {
        params: { query: { symbols: key } },
      });
      return data as InstrumentLookupResponse;
    },
    enabled: key.length > 0,
    staleTime: 5 * 60_000, // company descriptions are stable; 5min cache
    refetchOnWindowFocus: false,
  });
}

/**
 * Build a stable comma-joined string for use in a query key. The hook
 * memoizes by length+content so an unchanged list doesn't re-render
 * the consumer.
 */
function useMemoSortedJoined(symbols: ReadonlyArray<string>): string {
  // Pure (no React imports needed) — but a downstream useMemo on the
  // caller side is the responsible move if the list comes from a
  // computed value. For now: re-derive each render; it's cheap.
  const unique = Array.from(new Set(symbols.map((s) => s.toUpperCase()))).sort();
  return unique.join(",");
}

// ─────────────────────────────────────────────────────────────────────
// /api/v1/clickhouse — ad-hoc query page (FE-CONTRACTS-6a)
// ─────────────────────────────────────────────────────────────────────

/**
 * Schema browser. Cached server-side (60s); cockpit caches a further
 * 30s so rapid expand/collapse doesn't refetch.
 */
export function useClickHouseSchema() {
  return useQuery({
    queryKey: queryKeys.clickhouseSchema,
    queryFn: async (): Promise<ClickHouseSchemaResponse> => {
      const { data } = await apiClient.GET("/api/v1/clickhouse/schema");
      return data as ClickHouseSchemaResponse;
    },
    staleTime: 30_000,
  });
}

/**
 * Execute a SQL query. Modeled as a mutation (rather than a query)
 * because each run is a side-effect-free but explicit user action —
 * we don't want TanStack auto-refetching on focus, mount, etc.
 *
 * The cockpit also wants `data` to clear between submissions so the
 * old result doesn't linger while a new query is in flight.
 */
export function useExecuteClickHouseQuery() {
  return useMutation({
    mutationFn: async (
      req: ClickHouseQueryRequest,
    ): Promise<ClickHouseQueryResponse> => {
      const { data } = await apiClient.POST("/api/v1/clickhouse/query", {
        body: req,
      });
      return data as ClickHouseQueryResponse;
    },
  });
}
