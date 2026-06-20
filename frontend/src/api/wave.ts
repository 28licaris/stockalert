import { useQuery } from "@tanstack/react-query";
import { ApiError, readErrorEnvelope } from "@/lib/errors";

/**
 * Elliott Wave API hooks (EW-5).
 *
 * Hand-typed rather than generated: the wave endpoints are new and
 * regenerating types.gen.ts pulls in unrelated openapi-typescript version
 * churn. These interfaces mirror app/services/readers/wave_reader.py
 * (WaveStateResponse / WaveCountView) 1:1. Error handling reuses the same
 * `ApiError` envelope the openapi-fetch client throws, so failures surface
 * identically in TanStack Query.
 */

export interface WavePivotRow {
  index: number;
  timestamp: string;
  price: number;
  kind: "high" | "low";
  k: number;
  degree: number;
  confirmed_at_index: number;
}

export interface WaveForward {
  next_move?: string;
  target_low?: number;
  target_high?: number;
  target_basis?: string[];
  invalidation?: number;
}

export interface WaveCountView {
  structure: string;
  direction: string;
  current_wave: string;
  degree: number | null;
  probability: number;
  confidence: number;
  invalidation: number | null;
  targets: Record<string, number>;
  rationale: string;
  nesting_score: number;
  forward: WaveForward;
  pivots: WavePivotRow[];
}

export interface WaveStateResponse {
  symbol: string;
  interval: string;
  asset_class: string;
  as_of_date: string | null;
  as_of_ts: string | null;
  primary: WaveCountView | null;
  secondary: WaveCountView | null;
  uncertainty: number;
  engine_ver: string;
  source: string;
}

export interface WaveAlert {
  symbol: string;
  asset_class: string;
  interval: string;
  setup: string;
  direction: "long" | "short";
  trade_type: "day" | "swing";
  probability: number;
  entry: number;
  stop: number;
  target_1: number;
  target_2: number | null;
  risk_reward: number;
  current_wave: string;
  as_of_date: string | null;
  rationale: string;
}

export type WaveBackend = "store" | "compute" | "auto";

/** Encode a symbol into the URL path while preserving the futures "/" prefix
 * (e.g. "/GC" → "/GC", so the backend's `{symbol:path}` route captures it).
 * Each segment is still encoded; only the slash separators survive. */
function symbolPath(symbol: string): string {
  return symbol.split("/").map(encodeURIComponent).join("/");
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw new ApiError(await readErrorEnvelope(res.clone()), res.status);
  }
  return (await res.json()) as T;
}

export function useWaveState(
  symbol: string | undefined,
  interval = "1d",
  backend: WaveBackend = "auto",
) {
  return useQuery({
    queryKey: ["wave", "state", symbol, interval, backend],
    enabled: Boolean(symbol),
    staleTime: 60_000,
    queryFn: () =>
      getJson<WaveStateResponse>(
        `/api/v1/wave/${symbolPath(symbol!)}?interval=${interval}&backend=${backend}`,
      ),
  });
}

export function useWaveHistory(symbol: string | undefined, interval = "1d") {
  return useQuery({
    queryKey: ["wave", "history", symbol, interval],
    enabled: Boolean(symbol),
    staleTime: 60_000,
    queryFn: () =>
      getJson<WaveStateResponse[]>(
        `/api/v1/wave/${symbolPath(symbol!)}/history?interval=${interval}`,
      ),
  });
}

export function useWaveAlerts(interval = "1d") {
  return useQuery({
    queryKey: ["wave", "alerts", interval],
    staleTime: 60_000,
    queryFn: () =>
      getJson<WaveAlert[]>(`/api/v1/wave/alerts?interval=${interval}`),
  });
}

/** Wave labels are positional: impulse pivots are 0-1-2-3-4-5, zigzag 0-A-B-C. */
export function waveLabel(structure: string, index: number): string {
  const seq =
    structure === "zigzag" ? ["0", "A", "B", "C"] : ["0", "1", "2", "3", "4", "5"];
  return seq[index] ?? String(index);
}
