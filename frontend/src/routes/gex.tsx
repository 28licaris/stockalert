import { useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { useLatestOptionGex } from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import {
  deriveGexLevels,
  fmtGex,
  GexLadder,
} from "@/components/options/GexLadder";
import { Button } from "@/components/ui/button";
import { fmtAgo, fmtDate } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * GEX dashboard — dealer gamma positioning at a glance (Mill-style):
 * header regime strip (spot, net GEX, positive/negative gamma regime,
 * flip level), key-level cards (call wall / put wall / flip), expiry
 * breakdown, and the strike ladder. Live rows come from the Schwab
 * chain snapshots; the ThetaData backfill feeds the same table with
 * 2016+ history (source=thetadata-eod).
 */

const DEFAULT_SYMBOL = "SPY";

export function GexPage() {
  const [draft, setDraft] = useState(DEFAULT_SYMBOL);
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);

  const strikeQ = useLatestOptionGex({ symbol, aggregationLevel: "strike", limit: 250 });
  const totalQ = useLatestOptionGex({ symbol, aggregationLevel: "total", limit: 1 });
  const expiryQ = useLatestOptionGex({ symbol, aggregationLevel: "expiry", limit: 12 });

  const strikeRows = strikeQ.data?.rows ?? [];
  const totalRow = (totalQ.data?.rows ?? [])[0];
  const expiryRows = useMemo(
    () =>
      [...(expiryQ.data?.rows ?? [])].sort((a, b) =>
        (a.expiration_date ?? "").localeCompare(b.expiration_date ?? ""),
      ),
    [expiryQ.data],
  );

  const levels = useMemo(() => deriveGexLevels(strikeRows), [strikeRows]);
  const netGex = totalRow ? (totalRow.net_gamma_exposure ?? totalRow.gamma_exposure) : null;
  const positive = (netGex ?? 0) >= 0;
  const spotAboveFlip =
    levels.spot != null && levels.flipStrike != null && levels.spot > levels.flipStrike;
  const asOf = totalRow?.snapshot_ts ?? strikeRows[0]?.snapshot_ts;

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">GEX</h1>
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setSymbol(draft.trim().toUpperCase() || DEFAULT_SYMBOL);
          }}
        >
          <div className="relative">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="h-8 w-28 rounded-md border border-border bg-background pl-7 pr-2 font-mono text-sm uppercase"
              aria-label="Underlying symbol"
            />
          </div>
          <Button type="submit" size="sm" variant="secondary">
            Load
          </Button>
        </form>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            void strikeQ.refetch();
            void totalQ.refetch();
            void expiryQ.refetch();
          }}
        >
          <RefreshCw
            className={cn("mr-1 h-3.5 w-3.5", strikeQ.isFetching && "animate-spin")}
          />
          Refresh
        </Button>
        {asOf && (
          <span className="ml-auto text-xs text-muted-foreground">
            snapshot {fmtAgo(asOf)}
          </span>
        )}
      </div>

      {strikeQ.error && <ApiErrorAlert error={strikeQ.error} />}

      {/* regime strip */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <HeaderCard label={`${symbol} spot`}>
          <span className="font-mono text-xl">
            {levels.spot != null ? `$${levels.spot.toFixed(2)}` : "—"}
          </span>
        </HeaderCard>
        <HeaderCard label="Net GEX (per 1% move)">
          <span
            className={cn(
              "font-mono text-xl",
              positive ? "text-emerald-300" : "text-rose-300",
            )}
          >
            {netGex != null ? fmtGex(netGex) : "—"}
          </span>
        </HeaderCard>
        <HeaderCard label="Regime">
          <span
            className={cn(
              "inline-flex items-center gap-2 rounded border px-2 py-1 text-sm font-medium",
              positive
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-rose-500/40 bg-rose-500/10 text-rose-300",
            )}
          >
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                positive ? "bg-emerald-400" : "bg-rose-400",
              )}
            />
            {positive ? "POSITIVE · MEAN REVERT" : "NEGATIVE · AMPLIFY"}
          </span>
        </HeaderCard>
        <HeaderCard label="Gamma flip">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-xl">
              {levels.flipStrike != null ? `$${levels.flipStrike}` : "—"}
            </span>
            {levels.flipStrike != null && (
              <span
                className={cn(
                  "text-xs",
                  spotAboveFlip ? "text-emerald-300" : "text-rose-300",
                )}
              >
                spot {spotAboveFlip ? "above" : "below"} flip
              </span>
            )}
          </div>
        </HeaderCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-3">
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-muted-foreground">
              Key levels
            </div>
            <KeyLevel
              label="Call wall"
              value={levels.callWall}
              note="dealer resistance"
              tone="emerald"
            />
            <KeyLevel
              label="Put wall"
              value={levels.putWall}
              note="dealer support"
              tone="rose"
            />
            <KeyLevel
              label="Gamma flip"
              value={levels.flipStrike}
              note={spotAboveFlip ? "spot above flip" : "spot below flip"}
              tone={spotAboveFlip ? "emerald" : "rose"}
            />
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-muted-foreground">
              GEX by expiry
            </div>
            {expiryRows.length === 0 && (
              <div className="text-xs text-muted-foreground">no expiry rows</div>
            )}
            <div className="space-y-1">
              {expiryRows.map((r) => {
                const g = r.net_gamma_exposure ?? r.gamma_exposure;
                return (
                  <div
                    key={r.level_key}
                    className="flex items-center justify-between font-mono text-xs"
                  >
                    <span className="text-muted-foreground">
                      {r.expiration_date ? fmtDate(r.expiration_date) : "—"}
                    </span>
                    <span className={g >= 0 ? "text-emerald-300" : "text-rose-300"}>
                      {fmtGex(g)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <GexLadder levels={levels} />
      </div>
    </div>
  );
}

function HeaderCard({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="mb-1 text-[10px] uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  );
}

function KeyLevel({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number | null;
  note: string;
  tone: "emerald" | "rose";
}) {
  return (
    <div className="flex items-baseline justify-between border-b border-border/40 py-1.5 last:border-0">
      <div>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-[10px] text-muted-foreground/60">{note}</div>
      </div>
      <span
        className={cn(
          "font-mono text-lg",
          tone === "emerald" ? "text-emerald-300" : "text-rose-300",
        )}
      >
        {value != null ? `$${value}` : "—"}
      </span>
    </div>
  );
}
