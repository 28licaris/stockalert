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

export type CalendarDay = components["schemas"]["CalendarDay"];
export type CalendarEvent = components["schemas"]["CalendarEvent"];
export type CalendarResponse = components["schemas"]["CalendarResponse"];
export type CalendarAssetClass = "equities" | "futures";

// News feed (official-record filings + govt; AI-summarized + source link)
export type NewsItem = components["schemas"]["NewsItem"];
export type NewsDigest = components["schemas"]["NewsDigest"];

// Economic indicators (free government data — BLS now)
export type EconIndicator = components["schemas"]["EconIndicator"];
export type EconHistoryPoint = components["schemas"]["EconHistoryPoint"];

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

// Authoritative ClickHouse stream universe
export type StreamUniverseEntry = components["schemas"]["StreamUniverseEntry"];
export type StreamUniverseResponse =
  components["schemas"]["StreamUniverseResponse"];
export type StreamMutationResponse =
  components["schemas"]["StreamMutationResponse"];
export type AddStreamRequest = components["schemas"]["AddStreamRequest"];
export type ImportStreamRequest = components["schemas"]["ImportStreamRequest"];

// Options hot-tier reads (manual until the OpenAPI client is regenerated)
export type PutCall = "CALL" | "PUT";
export type GammaAggregationLevel =
  | "total"
  | "strike"
  | "expiry"
  | "strike_expiry";

export interface OptionContractSnapshot {
  underlying_symbol: string;
  option_symbol: string;
  snapshot_ts: string;
  put_call: PutCall;
  expiration_date: string;
  strike: number;
  underlying_price: number | null;
  days_to_expiration: number | null;
  bid: number | null;
  ask: number | null;
  last: number | null;
  mark: number | null;
  bid_size: number | null;
  ask_size: number | null;
  last_size: number | null;
  volume: number | null;
  open_interest: number | null;
  quote_time: string | null;
  trade_time: string | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  rho: number | null;
  volatility: number | null;
  theoretical_value: number | null;
  intrinsic_value: number | null;
  time_value: number | null;
  in_the_money: boolean | null;
  mini: boolean | null;
  non_standard: boolean | null;
  penny_pilot: boolean | null;
  multiplier: number | null;
  settlement_type: string | null;
  expiration_type: string | null;
  source: string;
  ingestion_ts: string | null;
  ingestion_run_id: string | null;
}

export interface GammaExposureSnapshot {
  underlying_symbol: string;
  snapshot_ts: string;
  expiration_date: string | null;
  strike: number | null;
  put_call: PutCall | null;
  underlying_price: number;
  gamma_exposure: number;
  call_gamma_exposure: number | null;
  put_gamma_exposure: number | null;
  net_gamma_exposure: number | null;
  open_interest: number | null;
  volume: number | null;
  contract_count: number | null;
  aggregation_level: GammaAggregationLevel;
  level_key: string;
  methodology: string;
  source: string;
  source_snapshot_id: string | null;
  ingestion_ts: string | null;
  ingestion_run_id: string | null;
}

export interface LatestOptionContractsResponse {
  underlying_symbol: string;
  contracts: OptionContractSnapshot[];
  count: number;
  source: string;
}

export interface LatestGammaExposureResponse {
  underlying_symbol: string;
  aggregation_level: GammaAggregationLevel | null;
  rows: GammaExposureSnapshot[];
  count: number;
  source: string;
}

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
  calendar: (assetClass: string, start: string, end: string) =>
    ["calendar", assetClass, start, end] as const,
  news: (symbols: string | undefined, types: string | undefined) =>
    ["news", symbols ?? "all", types ?? "all"] as const,
  newsDigest: ["news", "digest"] as const,
  economic: ["economic"] as const,
  economicHistory: (seriesId: string) => ["economic", "history", seriesId] as const,
  symbolBars: (symbol: string, interval: string, limit: number) =>
    ["symbol", "bars", symbol, interval, limit] as const,
  lakeBars: (
    symbol: string,
    interval: string,
    windowDays: number,
    source: "auto" | "lake",
  ) => ["lake", "bars", symbol, interval, windowDays, source] as const,
  symbolSignals: (symbol: string, limit: number) =>
    ["symbol", "signals", symbol, limit] as const,
  indicators: (symbol: string, interval: string, windowDays: number, ids: string, settings: string) =>
    ["symbol", "indicators", symbol, interval, windowDays, ids, settings] as const,
  watchlists: ["watchlists"] as const,
  watchlist: (name: string) => ["watchlist", name] as const,
  streamUniverse: ["stream", "universe"] as const,
  instrumentSearch: (query: string, limit: number) =>
    ["instruments", "search", query, limit] as const,
  instrumentLookup: (symbols: string) =>
    ["instruments", "lookup", symbols] as const,
  clickhouseSchema: ["clickhouse", "schema"] as const,
  latestOptionContracts: (
    symbol: string,
    expirationDate: string | undefined,
    putCall: string | undefined,
    limit: number,
  ) =>
    [
      "options",
      "contracts",
      "latest",
      symbol,
      expirationDate ?? "",
      putCall ?? "",
      limit,
    ] as const,
  latestOptionGex: (
    symbol: string,
    aggregationLevel: string | undefined,
    limit: number,
  ) =>
    [
      "options",
      "gex",
      "latest",
      symbol,
      aggregationLevel ?? "",
      limit,
    ] as const,
  jobs: ["jobs"] as const,
  sectorRotation: (benchmark: string, tailWeeks: number) =>
    ["sectors", "rotation", benchmark, tailWeeks] as const,
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

/**
 * Market calendar (open / closed / early-close) for a date range. Sessions
 * are deterministic, so this caches aggressively.
 */
export function useCalendar(
  assetClass: CalendarAssetClass,
  start: string,
  end: string,
) {
  return useQuery({
    queryKey: queryKeys.calendar(assetClass, start, end),
    queryFn: async (): Promise<CalendarResponse> => {
      const { data } = await apiClient.GET("/api/v1/calendar", {
        params: { query: { start, end, asset_class: assetClass } },
      });
      return data as CalendarResponse;
    },
    staleTime: 60 * 60 * 1000, // 1h — calendar rarely changes
  });
}

/**
 * News feed — official-record items (SEC EDGAR filings + govt), AI-summarized
 * with a link to the source. `symbols`/`types` are optional comma-separated
 * filters; market-wide items always come back. Newest first.
 */
export function useNews(opts?: {
  symbols?: string;
  types?: string;
  limit?: number;
}) {
  const { symbols, types, limit = 100 } = opts ?? {};
  return useQuery({
    queryKey: queryKeys.news(symbols, types),
    queryFn: async (): Promise<NewsItem[]> => {
      const { data } = await apiClient.GET("/api/v1/news", {
        params: { query: { symbols, types, limit } },
      });
      return (data as NewsItem[]) ?? [];
    },
    staleTime: 5 * 60 * 1000, // 5m
  });
}

/**
 * Daily digest — today's material (high-importance) items, AI-summarized.
 */
export function useNewsDigest() {
  return useQuery({
    queryKey: queryKeys.newsDigest,
    queryFn: async (): Promise<NewsDigest> => {
      const { data } = await apiClient.GET("/api/v1/news/digest", {});
      return data as NewsDigest;
    },
    staleTime: 5 * 60 * 1000, // 5m
  });
}

/** Economic indicators — latest figure + change per series. */
export function useEconomic() {
  return useQuery({
    queryKey: queryKeys.economic,
    queryFn: async (): Promise<EconIndicator[]> => {
      const { data } = await apiClient.GET("/api/v1/economic", {});
      return (data as EconIndicator[]) ?? [];
    },
    staleTime: 30 * 60 * 1000, // 30m — releases are infrequent
  });
}

/** Release history for one economic series (newest first). */
export function useEconomicHistory(seriesId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.economicHistory(seriesId ?? ""),
    enabled: !!seriesId,
    queryFn: async (): Promise<EconHistoryPoint[]> => {
      const { data } = await apiClient.GET("/api/v1/economic/{series_id}/history", {
        params: { path: { series_id: seriesId as string } },
      });
      return (data as EconHistoryPoint[]) ?? [];
    },
    staleTime: 30 * 60 * 1000,
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

export interface LatestOptionContractsParams {
  symbol: string | undefined;
  expirationDate?: string | undefined;
  putCall?: PutCall | undefined;
  limit: number;
}

export function useLatestOptionContracts({
  symbol,
  expirationDate,
  putCall,
  limit,
}: LatestOptionContractsParams) {
  const normalized = symbol?.trim().toUpperCase();
  return useQuery({
    queryKey: queryKeys.latestOptionContracts(
      normalized ?? "",
      expirationDate,
      putCall,
      limit,
    ),
    queryFn: async (): Promise<LatestOptionContractsResponse> => {
      if (!normalized) throw new Error("symbol required");
      const params = new URLSearchParams({
        symbol: normalized,
        limit: String(limit),
      });
      if (expirationDate) params.set("expiration_date", expirationDate);
      if (putCall) params.set("put_call", putCall);
      return fetchJson<LatestOptionContractsResponse>(
        `/api/v1/options/contracts/latest?${params.toString()}`,
      );
    },
    enabled: Boolean(normalized),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 10_000,
  });
}

export interface LatestOptionGexParams {
  symbol: string | undefined;
  aggregationLevel?: GammaAggregationLevel | undefined;
  limit: number;
}

export function useLatestOptionGex({
  symbol,
  aggregationLevel,
  limit,
}: LatestOptionGexParams) {
  const normalized = symbol?.trim().toUpperCase();
  return useQuery({
    queryKey: queryKeys.latestOptionGex(
      normalized ?? "",
      aggregationLevel,
      limit,
    ),
    queryFn: async (): Promise<LatestGammaExposureResponse> => {
      if (!normalized) throw new Error("symbol required");
      const params = new URLSearchParams({
        symbol: normalized,
        limit: String(limit),
      });
      if (aggregationLevel) params.set("aggregation_level", aggregationLevel);
      return fetchJson<LatestGammaExposureResponse>(
        `/api/v1/options/gex/latest?${params.toString()}`,
      );
    },
    enabled: Boolean(normalized),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 10_000,
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

export const CHART_RANGES = ["1D", "5D", "30D", "180D", "1Y", "5Y", "MAX"] as const;
export type ChartRange = (typeof CHART_RANGES)[number];

export const CHART_RANGE_DAYS: Record<ChartRange, number> = {
  "1D": 1,
  "5D": 5,
  "30D": 30,
  "180D": 180,
  "1Y": 365,
  "5Y": 365 * 5,
  // Operational "max" for the current lake-backed chart surface: ~20 years.
  MAX: 365 * 20,
};

function chartBarsSource(_interval: string, _range: ChartRange): "auto" | "lake" {
  // Keep interactive charts on the hot gateway. True 5Y/MAX daily history
  // needs a fast daily lake aggregate/materialized tier; the raw lake path
  // currently scans minute bars and is too slow for the UI.
  return "auto";
}

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
export function useLakeBars(
  symbol: string | undefined,
  interval: string,
  range: ChartRange = "30D",
) {
  const windowDays = CHART_RANGE_DAYS[range] ?? 30;
  const source = chartBarsSource(interval, range);

  return useQuery({
    queryKey: queryKeys.lakeBars(symbol ?? "", interval, windowDays, source),
    queryFn: async (): Promise<Bar[]> => {
      if (!symbol) throw new Error("symbol required");
      const { data } = await apiClient.GET("/api/v1/bars", {
        params: {
          query: {
            symbol,
            interval,
            lookback_days: windowDays,
            source,
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
// and refresh in lockstep. Indicator settings are passed through as backend
// params (for example SMA/EMA/WMA `period`) and we read only `series`
// (bars come from `useLakeBars`).
// ─────────────────────────────────────────────────────────────────────

export function useIndicators(
  symbol: string | undefined,
  interval: string,
  indicatorIds: ReadonlyArray<string>,
  range: ChartRange = "30D",
  indicatorSettings: Record<string, Record<string, number>> = {},
) {
  const windowDays = CHART_RANGE_DAYS[range] ?? 30;
  // Stable, order-independent key so re-ordering selections doesn't refetch.
  const ids = [...indicatorIds].sort();
  const idsKey = ids.join(",");
  const settingsKey = JSON.stringify(
    Object.fromEntries(ids.map((id) => [id, indicatorSettings[id] ?? {}])),
  );

  return useQuery({
    queryKey: queryKeys.indicators(symbol ?? "", interval, windowDays, idsKey, settingsKey),
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
          indicators: ids.map((name) => ({
            name,
            params: indicatorSettings[name] ?? {},
          })),
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
// /api/v1/stream — authoritative ClickHouse stream universe
// ─────────────────────────────────────────────────────────────────────

export function useStreamUniverse() {
  return useQuery({
    queryKey: queryKeys.streamUniverse,
    queryFn: async (): Promise<StreamUniverseResponse> => {
      const { data } = await apiClient.GET("/api/v1/stream");
      return data as StreamUniverseResponse;
    },
    staleTime: 10_000,
  });
}

function _invalidateStreamUniverse(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: queryKeys.streamUniverse });
}

export function useAddStream() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: AddStreamRequest): Promise<StreamMutationResponse> => {
      const { data } = await apiClient.POST("/api/v1/stream", { body: req });
      return data as StreamMutationResponse;
    },
    onSuccess: () => _invalidateStreamUniverse(qc),
  });
}

export function useRemoveStream() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string): Promise<StreamMutationResponse> => {
      const { data } = await apiClient.DELETE("/api/v1/stream/{symbol}", {
        params: { path: { symbol } },
      });
      return data as StreamMutationResponse;
    },
    onSuccess: () => _invalidateStreamUniverse(qc),
  });
}

export function useImportStream() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      req: ImportStreamRequest,
    ): Promise<StreamMutationResponse> => {
      const { data } = await apiClient.POST("/api/v1/stream/import", {
        body: req,
      });
      return data as StreamMutationResponse;
    },
    onSuccess: () => _invalidateStreamUniverse(qc),
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

// ─────────────────────────────────────────────────────────────────────
// /api/v1/sectors/rotation — RRG sector-rotation dashboard
// ─────────────────────────────────────────────────────────────────────

export type RotationDashboard = components["schemas"]["RotationDashboard"];
export type SectorRotationState =
  components["schemas"]["SectorRotationState"];
export type RotationPoint = components["schemas"]["RotationPoint"];
export type RotationQuadrant = RotationPoint["quadrant"];

/**
 * Sector rotation (RRG) dashboard. The backend reads ClickHouse and the
 * picture only changes once new daily bars land, so we keep it fresh but
 * un-aggressive: refetch on a slow interval, generous staleTime.
 */
export function useSectorRotation(benchmark = "SPY", tailWeeks = 12) {
  return useQuery({
    queryKey: queryKeys.sectorRotation(benchmark, tailWeeks),
    queryFn: async (): Promise<RotationDashboard> => {
      const { data } = await apiClient.GET("/api/v1/sectors/rotation", {
        params: { query: { benchmark, tail_weeks: tailWeeks } },
      });
      return data as RotationDashboard;
    },
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

// Themes as data — create/delete thematic baskets at runtime.
export type ThemeRecord = components["schemas"]["ThemeRecord"];
export type ThemeCreateRequest = components["schemas"]["ThemeCreateRequest"];
export type ThemeMutationResponse = components["schemas"]["ThemeMutationResponse"];

export function useSectorThemes() {
  return useQuery({
    queryKey: ["sectors", "themes"] as const,
    queryFn: async (): Promise<ThemeRecord[]> => {
      const { data } = await apiClient.GET("/api/v1/sectors/themes");
      return (data as ThemeRecord[]) ?? [];
    },
    staleTime: 60_000,
  });
}

export function useCreateTheme() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (req: ThemeCreateRequest): Promise<ThemeMutationResponse> => {
      const { data } = await apiClient.POST("/api/v1/sectors/themes", { body: req });
      return data as ThemeMutationResponse;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sectors"] }),
  });
}

export function useDeleteTheme() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (themeId: string): Promise<ThemeMutationResponse> => {
      const { data } = await apiClient.DELETE("/api/v1/sectors/themes/{theme_id}", {
        params: { path: { theme_id: themeId } },
      });
      return data as ThemeMutationResponse;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sectors"] }),
  });
}
