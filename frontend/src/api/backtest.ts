// Backtest API client — the customer playground data layer.
// Local types (decoupled from generated types so the page builds independently);
// plain fetch against the same-origin /api/v1 surface (Vite proxies in dev).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export interface RunMetrics {
  total_return: number;
  annualized_return?: number | null;
  sharpe_ratio?: number | null;
  sortino_ratio?: number | null;
  max_drawdown: number;
  longest_drawdown_days: number;
  win_rate?: number | null;
  profit_factor?: number | null;
  n_trades: number;
  avg_trade_pnl?: number | null;
  avg_winner_pnl?: number | null;
  avg_loser_pnl?: number | null;
  avg_holding_days?: number | null;
  final_equity: number;
}

export interface EquityPoint { t: string; equity: number }

export interface TradeOut {
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  timestamp: string;
  realized_pnl: number;
  holding_days: number;
  is_closing: boolean;
  note: string;
}

export interface BacktestRunResponse {
  run_id: string;
  strategy: string;
  symbols: string[];
  start: string;
  end: string;
  interval: string;
  portfolio: boolean;
  stored: boolean;
  metrics: RunMetrics;
  equity_curve: EquityPoint[];
  trades: TradeOut[];
}

export interface RunSummary {
  run_id: string;
  started_at?: string | null;
  strategy_name: string;
  interval?: string | null;
  total_return?: number | null;
  sharpe_ratio?: number | null;
  max_drawdown?: number | null;
  win_rate?: number | null;
  n_trades?: number | null;
}

export interface BacktestCatalog {
  strategies: string[];
  signal_sources: string[];
  filters: string[];
}

export interface BacktestRunRequest {
  strategy: string;
  strategy_params: Record<string, unknown>;
  symbols: string[];
  start: string;
  end: string;
  interval: string;
  benchmark?: string | null;
  portfolio: boolean;
  starting_cash: number;
  max_concurrent_positions: number;
  max_portfolio_heat: number;
  store: boolean;
}

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path, { credentials: "include" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = await r.text();
    try { detail = JSON.parse(detail).detail ?? detail; } catch { /* keep text */ }
    throw new Error(detail || `${r.status}`);
  }
  return r.json() as Promise<T>;
}

export function useBacktestCatalog() {
  return useQuery({
    queryKey: ["backtest", "catalog"],
    queryFn: () => getJSON<BacktestCatalog>("/api/v1/backtest/catalog"),
    staleTime: 60 * 60 * 1000,
  });
}

export function useBacktestRuns(limit = 25) {
  return useQuery({
    queryKey: ["backtest", "runs", limit],
    queryFn: () => getJSON<RunSummary[]>(`/api/v1/backtest/runs?limit=${limit}`),
    staleTime: 10_000,
  });
}

// ── Paper trading (M3) ───────────────────────────────────────────────

export interface PaperPositionView {
  symbol: string;
  quantity: number;
  avg_entry_price: number;
  current_price: number;
  entry_time: string;
  unrealized_pnl: number;
}

export interface PaperTradeView {
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  timestamp: string;
  realized_pnl: number;
  holding_days: number;
  is_closing: boolean;
  entry_date: string | null;
  exit_date: string | null;
}

export interface PaperStatus {
  name: string;
  go_live: string;
  start_date: string;
  last_run_at: string | null;
  computed_through: string | null;
  days_live: number;
  starting_capital: number;
  current_balance: number;
  forward_return: number;
  forward_n_trades: number;
  forward_win_rate: number | null;
  n_open_positions: number;
  open_positions: PaperPositionView[];
  forward_trades: PaperTradeView[];
  equity_curve: EquityPoint[];
  today_entries: PaperPositionView[];
  today_exits: PaperTradeView[];
}

export function usePaperStatus(name = "momentum_top15", start?: string, capital?: number) {
  const params = new URLSearchParams({ name });
  if (start) params.set("start", start);
  if (capital) params.set("capital", String(capital));
  return useQuery({
    queryKey: ["paper", "status", name, start ?? "", capital ?? ""],
    queryFn: () => getJSON<PaperStatus>(`/api/v1/paper/status?${params.toString()}`),
    staleTime: 30_000,
    retry: false,
  });
}

export function useRunBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: BacktestRunRequest) =>
      postJSON<BacktestRunResponse>("/api/v1/backtest/run", req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backtest", "runs"] });
    },
  });
}
