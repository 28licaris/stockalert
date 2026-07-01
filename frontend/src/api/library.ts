// Strategy library API — subscriber (redacted) + owner (full stats) surfaces.
import { useQuery } from "@tanstack/react-query";

export interface StrategyPublic {
  name: string;
  title: string;
  tagline: string;
  description: string;
  category: string;
  version: number;
  visibility: string;
  inception: string | null;
  days_live: number;
  forward_return: number | null;
  forward_win_rate: number | null;
  forward_n_trades: number;
  n_open_positions: number;
}

export interface StrategyAlert {
  symbol: string;
  direction: string;
  status: string;
  date: string;
  entry: number | null;
  stop: number | null;
  target: number | null;
  current: number | null;
  exit: number | null;
  pnl: number | null;
}

export interface StrategyOwnerStats {
  name: string;
  title: string;
  backtest: Record<string, number> | null;
  paper_return: number | null;
  paper_win_rate: number | null;
  paper_trades: number;
  paper_days: number;
  starting_capital: number | null;
  current_balance: number | null;
  last_run_at: string | null;
  computed_through: string | null;
}

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path, { credentials: "include" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export function useLibrary() {
  return useQuery({
    queryKey: ["library", "list"],
    queryFn: () => getJSON<StrategyPublic[]>("/api/v1/library"),
    staleTime: 60_000,
  });
}

export function useStrategyAlerts(name: string, enabled: boolean) {
  return useQuery({
    queryKey: ["library", "alerts", name],
    queryFn: () => getJSON<StrategyAlert[]>(`/api/v1/library/${encodeURIComponent(name)}/alerts`),
    enabled,
    staleTime: 60_000,
  });
}

export function useLeaderboard(enabled: boolean) {
  return useQuery({
    queryKey: ["library", "leaderboard"],
    queryFn: () => getJSON<StrategyOwnerStats[]>("/api/v1/strategies/leaderboard"),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}

export function useStrategyOwnerStats(name: string, enabled: boolean) {
  return useQuery({
    queryKey: ["library", "ownerstats", name],
    queryFn: () => getJSON<StrategyOwnerStats>(`/api/v1/strategies/${encodeURIComponent(name)}/stats`),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}
