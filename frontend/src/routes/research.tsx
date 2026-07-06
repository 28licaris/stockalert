import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Sparkles, LineChart as LineIcon } from "lucide-react";
import {
  useAddMyWatchlistMembers,
  useMyWatchlists,
  useResearchRankings,
  type RankingRow,
} from "@/api/queries";
import { useChatStore } from "@/stores/chat";
import { useUserSetting } from "@/lib/storage";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { fmtPrice } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Research — premium screener over the top-1000 liquid universe. Preset views
 * (momentum / gainers / losers / most active / streaks) backed by guarded
 * ClickHouse rankings. Results are "as of last close" (see the header note).
 * A composable filter builder + live-movers tab land in later phases.
 */
const PRESETS = [
  { key: "momentum", label: "Momentum", highlight: "ret" },
  { key: "gainers", label: "Top Gainers", highlight: "chg" },
  { key: "losers", label: "Top Losers", highlight: "chg" },
  { key: "most_active", label: "Most Active", highlight: "vol" },
  { key: "streak_up", label: "Up Streak", highlight: "up" },
  { key: "streak_down", label: "Down Streak", highlight: "down" },
] as const;

type PresetKey = (typeof PRESETS)[number]["key"];

function fmtVol(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}
function pct(v: number | null): string {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function ResearchPage() {
  const [preset, setPreset] = useState<PresetKey>("momentum");
  const [lookback, setLookback] = useState(60);
  const [streakMin, setStreakMin] = useState(3);

  const q = useResearchRankings({
    preset,
    lookback_days: lookback,
    top_n: 50,
    streak_min: streakMin,
  });
  const rows = q.data?.rows ?? [];
  const isStreak = preset === "streak_up" || preset === "streak_down";

  // Row actions — hooks lifted here so 50 rows share one instance.
  const lists = useMyWatchlists();
  const add = useAddMyWatchlistMembers();
  const firstList = lists.data?.[0]?.name ?? null;
  const send = useChatStore((s) => s.send);
  const [, setChatOpen] = useUserSetting<boolean>("ui.chat.open", false);
  const onAdd = (symbol: string) =>
    firstList && add.mutate({ name: firstList, symbols: [symbol], quantity: 100 });
  const onAsk = (symbol: string, name: string) => {
    setChatOpen(true);
    void send(`Give me a quick read on ${symbol}${name ? ` (${name})` : ""} — why is it moving and is it extended?`);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 md:p-6">
      <header className="surface-panel rounded-lg p-5">
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">market research</p>
        <h1 className="mt-2 font-display text-2xl font-semibold text-fg-base">Research</h1>
        <p className="mt-1 max-w-2xl text-sm text-fg-muted">
          Screen the top-1000 most-liquid names for momentum, movers, and streaks.
          {q.data ? (
            <span className="text-fg-subtle"> Rankings as of the last close ({q.data.as_of}).</span>
          ) : null}
        </p>
      </header>

      {/* Preset tabs */}
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => setPreset(p.key)}
            className={cn(
              "rounded-full border px-3.5 py-1.5 text-sm transition-colors",
              preset === p.key
                ? "border-accent/60 bg-accent/10 text-fg-base"
                : "border-border bg-bg-subtle/70 text-fg-muted hover:text-fg-base",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        {preset === "momentum" ? (
          <label className="flex items-center gap-2 text-fg-muted">
            Lookback
            <select
              value={lookback}
              onChange={(e) => setLookback(Number(e.target.value))}
              className="rounded-md border border-border bg-bg-base px-2 py-1 text-fg-base focus:border-accent focus:outline-none"
            >
              <option value={20}>20 days</option>
              <option value={60}>60 days</option>
              <option value={120}>120 days</option>
            </select>
          </label>
        ) : null}
        {isStreak ? (
          <label className="flex items-center gap-2 text-fg-muted">
            Min streak
            <input
              type="number"
              min={1}
              max={30}
              value={streakMin}
              onChange={(e) => setStreakMin(Number(e.target.value) || 3)}
              className="w-16 rounded-md border border-border bg-bg-base px-2 py-1 tabular-nums text-fg-base focus:border-accent focus:outline-none"
            />
          </label>
        ) : null}
        {q.isFetching ? (
          <span className="text-xs uppercase tracking-wider text-fg-subtle">Refreshing…</span>
        ) : null}
      </div>

      {q.error ? <ApiErrorAlert error={q.error} /> : null}

      {/* Results */}
      <div className="surface-panel overflow-hidden rounded-lg">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-bg-muted/50 text-[10px] uppercase tracking-wider text-fg-subtle">
              <tr>
                <th className="w-10 px-3 py-2 text-right font-medium">#</th>
                <th className="px-4 py-2 text-left font-medium">Symbol</th>
                <th className="px-4 py-2 text-right font-medium">Price</th>
                <th className="px-4 py-2 text-right font-medium">1D</th>
                <th className="px-4 py-2 text-right font-medium">Return</th>
                <th className="px-4 py-2 text-right font-medium">Streak</th>
                <th className="px-4 py-2 text-right font-medium">$ Vol</th>
                <th className="px-4 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {q.isLoading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-sm text-fg-subtle">
                    Loading…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-sm text-fg-subtle">
                    No matches.
                  </td>
                </tr>
              ) : (
                rows.map((r, i) => (
                  <ResultRow
                    key={r.symbol}
                    rank={i + 1}
                    row={r}
                    highlight={PRESETS.find((p) => p.key === preset)!.highlight}
                    canAdd={!!firstList}
                    onAdd={onAdd}
                    onAsk={onAsk}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ResultRow({
  rank,
  row,
  highlight,
  canAdd,
  onAdd,
  onAsk,
}: {
  rank: number;
  row: RankingRow;
  highlight: string;
  canAdd: boolean;
  onAdd: (symbol: string) => void;
  onAsk: (symbol: string, name: string) => void;
}) {
  const [added, setAdded] = useState(false);
  const streak = row.up_streak >= row.down_streak ? row.up_streak : -row.down_streak;
  const hl = (key: string) => (highlight === key ? "font-semibold text-fg-base" : "");

  return (
    <tr className="hover:bg-bg-muted/40">
      <td className="px-3 py-2 text-right tabular-nums text-fg-subtle">{rank}</td>
      <td className="px-4 py-2">
        <Link to={`/charts/${encodeURIComponent(row.symbol)}`} className="font-medium text-fg-base hover:text-accent">
          {row.symbol}
        </Link>
        <div className="max-w-[220px] truncate text-xs text-fg-subtle">{row.name}</div>
      </td>
      <td className="px-4 py-2 text-right tabular-nums text-fg-muted">
        {row.price != null ? fmtPrice(row.price) : "—"}
      </td>
      <td className={cn("px-4 py-2 text-right tabular-nums", (row.chg_1d_pct ?? 0) >= 0 ? "text-up" : "text-down", hl("chg"))}>
        {pct(row.chg_1d_pct)}
      </td>
      <td className={cn("px-4 py-2 text-right tabular-nums", (row.ret_pct ?? 0) >= 0 ? "text-up" : "text-down", hl("ret"))}>
        {pct(row.ret_pct)}
      </td>
      <td className={cn("px-4 py-2 text-right tabular-nums", streak >= 0 ? "text-up" : "text-down", (highlight === "up" || highlight === "down") ? "font-semibold" : "")}>
        {streak === 0 ? "—" : `${streak > 0 ? "+" : ""}${streak}`}
      </td>
      <td className={cn("px-4 py-2 text-right tabular-nums text-fg-muted", hl("vol"))}>{fmtVol(row.dollar_vol)}</td>
      <td className="px-4 py-2">
        <div className="flex items-center justify-end gap-1">
          <Link
            to={`/charts/${encodeURIComponent(row.symbol)}`}
            className="rounded p-1.5 text-fg-subtle hover:bg-bg-muted/70 hover:text-accent"
            title="Open chart"
          >
            <LineIcon className="h-3.5 w-3.5" />
          </Link>
          <button
            type="button"
            disabled={!canAdd || added}
            onClick={() => {
              onAdd(row.symbol);
              setAdded(true);
            }}
            className="rounded p-1.5 text-fg-subtle hover:bg-bg-muted/70 hover:text-accent disabled:opacity-40"
            title={canAdd ? (added ? "Added" : "Add to watchlist") : "Create a watchlist first"}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onAsk(row.symbol, row.name)}
            className="rounded p-1.5 text-fg-subtle hover:bg-bg-muted/70 hover:text-accent"
            title="Ask the AI assistant"
          >
            <Sparkles className="h-3.5 w-3.5" />
          </button>
        </div>
      </td>
    </tr>
  );
}
