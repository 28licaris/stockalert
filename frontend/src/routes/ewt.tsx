import { useNavigate, useParams } from "react-router-dom";
import { useState } from "react";
import { OhlcvChart, type WaveOverlay } from "@/components/charts/OhlcvChart";
import { Button } from "@/components/ui/button";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { SymbolSearchInput } from "@/components/symbol/SymbolSearchInput";
import { useLakeBars } from "@/api/queries";
import {
  useWaveState,
  useWaveAlerts,
  useIntradayWaveAlerts,
  waveLabel,
  type WaveAlert,
  type WaveCountView,
  type WaveScenario,
} from "@/api/wave";
import { useUserSetting } from "@/lib/storage";
import { fmtPrice } from "@/lib/fmt";
import { cn } from "@/lib/utils";

const INTERVALS = ["1d", "1h", "15m", "5m"] as const;
type Interval = (typeof INTERVALS)[number];

const SCAN_INTERVALS = ["1d", "1h", "15m", "5m"] as const;
type ScanInterval = (typeof SCAN_INTERVALS)[number];

/**
 * Elliott Wave analysis page (EW-5 / V3-3).
 *
 * Detail view (ticker selected):
 *   - Wave overlay on the chart (primary count path + price levels).
 *   - Aside panel with two tabs: "Counts" (primary/secondary/uncertainty) and
 *     "Scenarios" (V3-3: full ordered list with gate prices and plain-English
 *     confirms/invalidates text).
 *
 * Picker view (no ticker):
 *   - Symbol search.
 *   - Scan list with interval tabs: Daily / 1h / 15m / 5m.
 */
export function EwtPage() {
  const params = useParams();
  const ticker = (params.ticker ?? "").toUpperCase();
  const [interval, setInterval] = useUserSetting<Interval>("ewt.interval", "1d");
  const [asideTab, setAsideTab] = useState<"counts" | "scenarios">("counts");

  const bars = useLakeBars(ticker || undefined, interval);
  const wave = useWaveState(ticker || undefined, interval);

  if (!ticker) return <WavePicker />;

  const primary = wave.data?.primary ?? null;
  const overlay = buildOverlay(primary);
  const latest = bars.data?.at(-1);
  const scenarios = wave.data?.scenarios ?? [];

  return (
    <div className="flex h-full flex-col gap-4 p-4 md:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-fg-base">
            {ticker} <span className="text-base font-normal text-fg-subtle">· Elliott Wave</span>
          </h1>
          <div className="mt-1 flex items-baseline gap-3 text-sm">
            <span className="font-mono text-lg text-fg-base">{fmtPrice(latest?.close)}</span>
            {wave.data ? (
              <span className="text-xs text-fg-subtle">
                source: {wave.data.source} · {wave.data.engine_ver}
              </span>
            ) : null}
          </div>
        </div>
        <IntervalPicker value={interval} onChange={setInterval} />
      </header>

      {bars.error ? <ApiErrorAlert error={bars.error} /> : null}
      {wave.error ? <ApiErrorAlert error={wave.error} /> : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="relative">
          <OhlcvChart bars={bars.data ?? []} wave={overlay} />
          {!bars.data || bars.data.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center rounded-md bg-bg-base/60 text-sm text-fg-muted backdrop-blur-[1px]">
              {bars.isLoading || bars.isFetching ? "Fetching history…" : `No data for ${ticker}`}
            </div>
          ) : null}
        </div>

        <aside className="space-y-3">
          {/* Tab switcher */}
          <div
            role="tablist"
            aria-label="Analysis view"
            className="inline-flex w-full rounded-md border border-border bg-bg-subtle p-0.5"
          >
            {(["counts", "scenarios"] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={asideTab === t}
                onClick={() => setAsideTab(t)}
                className={cn(
                  "flex-1 rounded-sm py-1 text-xs font-medium capitalize",
                  asideTab === t
                    ? "bg-accent text-accent-fg"
                    : "text-fg-muted hover:bg-bg-muted hover:text-fg-base",
                )}
              >
                {t}
                {t === "scenarios" && scenarios.length > 0 ? (
                  <span className="ml-1 font-mono opacity-60">({scenarios.length})</span>
                ) : null}
              </button>
            ))}
          </div>

          {wave.isLoading || wave.isFetching ? (
            <p className="text-sm text-fg-muted">Computing wave count…</p>
          ) : asideTab === "counts" ? (
            <>
              <CountCard title="Primary" count={primary} accent />
              <CountCard title="Secondary" count={wave.data?.secondary ?? null} />
              <UncertaintyBar value={wave.data?.uncertainty ?? 1} />
            </>
          ) : (
            <ScenariosPanel scenarios={scenarios} />
          )}
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scenario panel (V3-3)
// ---------------------------------------------------------------------------

function ScenariosPanel({ scenarios }: { scenarios: WaveScenario[] }) {
  if (scenarios.length === 0) {
    return (
      <p className="text-sm text-fg-muted">No scenarios — recompute at a finer interval.</p>
    );
  }
  return (
    <div className="space-y-2.5">
      {scenarios.map((s) => (
        <ScenarioCard key={s.rank} scenario={s} />
      ))}
    </div>
  );
}

function ScenarioCard({ scenario: s }: { scenario: WaveScenario }) {
  const isPrimary = s.rank === 1;
  return (
    <div
      className={cn(
        "rounded-md border bg-bg-subtle p-3",
        isPrimary ? "border-accent/50" : "border-border",
      )}
    >
      {/* Header row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
              isPrimary ? "bg-accent/20 text-accent" : "bg-bg-muted text-fg-muted",
            )}
          >
            {s.label}
          </span>
          <span
            className={cn(
              "rounded-sm px-1.5 py-0.5 text-[10px] uppercase",
              s.direction === "up" ? "bg-up/15 text-up" : "bg-down/15 text-down",
            )}
          >
            {s.direction} · w{s.current_wave}
          </span>
        </div>
        <span className="font-mono text-xs text-fg-muted">
          P {(s.probability * 100).toFixed(0)}%
        </span>
      </div>

      {/* Structure */}
      <div className="mt-1 font-mono text-xs text-fg-base capitalize">{s.structure}</div>

      {/* Gate prices */}
      <dl className="mt-2 space-y-0.5 text-xs text-fg-muted">
        <Row label="Stop" value={fmtPrice(s.invalidation)} />
        {s.next_target != null ? (
          <Row label="Target" value={fmtPrice(s.next_target)} />
        ) : null}
        {s.confirms_at != null ? (
          <Row label="Active above" value={fmtPrice(s.confirms_at)} />
        ) : (
          <div className="flex items-center gap-1 text-[10px] text-fg-subtle">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" />
            Active now
          </div>
        )}
      </dl>

      {/* Plain-English gate text */}
      <div className="mt-2 space-y-1">
        {s.what_confirms && !isPrimary ? (
          <p className="text-[10px] leading-relaxed text-fg-subtle">
            <span className="font-medium text-up">Confirms:</span> {s.what_confirms}
          </p>
        ) : null}
        <p className="text-[10px] leading-relaxed text-fg-subtle">
          <span className="font-medium text-down">Invalidates:</span> {s.what_invalidates}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function buildOverlay(primary: WaveCountView | null): WaveOverlay | null {
  if (!primary) return null;
  return {
    pivots: primary.pivots.map((p, i) => ({
      ts: p.timestamp,
      price: p.price,
      label: waveLabel(primary.structure, i),
    })),
    levels: [
      ...(primary.invalidation != null
        ? [{ price: primary.invalidation, title: "stop", kind: "invalidation" as const }]
        : []),
      ...Object.entries(primary.targets).map(([k, v]) => ({
        price: v,
        title: k,
        kind: "target" as const,
      })),
    ],
  };
}

function CountCard({
  title,
  count,
  accent = false,
}: {
  title: string;
  count: WaveCountView | null;
  accent?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-md border bg-bg-subtle p-3",
        accent ? "border-accent/50" : "border-border",
      )}
    >
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
          {title}
        </span>
        {count ? (
          <span className="font-mono text-xs text-fg-muted">
            P {(count.probability * 100).toFixed(0)}%
          </span>
        ) : null}
      </div>
      {count ? (
        <>
          <div className="font-mono text-sm text-fg-base">
            {count.structure} {count.direction} · wave {count.current_wave}
          </div>
          <div
            className="mt-1.5 h-1.5 rounded-full bg-bg-muted"
            role="meter"
            aria-valuenow={Math.round(count.probability * 100)}
          >
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${Math.min(100, count.probability * 100)}%` }}
            />
          </div>
          <dl className="mt-2 space-y-0.5 text-xs text-fg-muted">
            <Row label="Stop" value={count.invalidation != null ? fmtPrice(count.invalidation) : "—"} />
            {Object.entries(count.targets).map(([k, v]) => (
              <Row key={k} label={k} value={fmtPrice(v)} />
            ))}
          </dl>
          {count.forward?.next_move ? (
            <div className="mt-2 rounded-sm border border-accent/40 bg-accent/5 px-2 py-1.5">
              <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
                Next move
              </div>
              <div className="font-mono text-xs text-fg-base">{count.forward.next_move}</div>
              {count.forward.target_low != null ? (
                <div className="font-mono text-xs text-fg-muted">
                  → {fmtPrice(count.forward.target_low)}–{fmtPrice(count.forward.target_high)}
                </div>
              ) : null}
            </div>
          ) : null}
          {count.rationale ? (
            <p className="mt-2 text-xs leading-relaxed text-fg-subtle">{count.rationale}</p>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-fg-muted">No clear count.</p>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="truncate">{label}</dt>
      <dd className="font-mono text-fg-base">{value}</dd>
    </div>
  );
}

function UncertaintyBar({ value }: { value: number }) {
  return (
    <div className="rounded-md border border-border bg-bg-subtle p-3">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-semibold uppercase tracking-wider text-fg-subtle">Uncertainty</span>
        <span className="font-mono text-fg-muted">{(value * 100).toFixed(0)}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-bg-muted">
        <div
          className="h-full rounded-full bg-fg-subtle"
          style={{ width: `${Math.min(100, value * 100)}%` }}
        />
      </div>
    </div>
  );
}

function IntervalPicker({
  value,
  onChange,
}: {
  value: Interval;
  onChange: (next: Interval) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Interval"
      className="inline-flex rounded-md border border-border bg-bg-subtle p-0.5"
    >
      {INTERVALS.map((i) => (
        <button
          key={i}
          type="button"
          role="tab"
          aria-selected={value === i}
          onClick={() => onChange(i)}
          className={cn(
            "rounded-sm px-2.5 py-1 font-mono text-xs",
            value === i
              ? "bg-accent text-accent-fg"
              : "text-fg-muted hover:bg-bg-muted hover:text-fg-base",
          )}
        >
          {i}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Picker (no ticker selected)
// ---------------------------------------------------------------------------

function WavePicker() {
  const navigate = useNavigate();
  const [input, setInput] = useState("");
  const go = (sym: string) => {
    const norm = sym.trim().toUpperCase();
    if (norm) navigate(`/ewt/${encodeURIComponent(norm)}`);
  };
  return (
    <div className="mx-auto max-w-xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold text-fg-base">Elliott Wave</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Search a ticker to see its primary + secondary wave count, invalidation,
          and Fibonacci targets.
        </p>
      </header>
      <div className="flex gap-2">
        <SymbolSearchInput
          value={input}
          onChange={setInput}
          onSubmit={(value, match) => go(match ? match.symbol : value)}
          placeholder="AAPL · NVDA · TSLA · /ES"
          autoFocus
          className="flex-1"
        />
        <Button type="button" onClick={() => go(input)} disabled={!input.trim()}>
          Analyze
        </Button>
      </div>

      <ScanList onPick={go} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scan list — daily + intraday tabs (EW-7)
// ---------------------------------------------------------------------------

function ScanList({ onPick }: { onPick: (sym: string) => void }) {
  const [scanInterval, setScanInterval] = useUserSetting<ScanInterval>(
    "ewt.scan_interval",
    "1d",
  );
  const isIntraday = scanInterval !== "1d";
  const daily = useWaveAlerts("1d");
  const intraday = useIntradayWaveAlerts(scanInterval, undefined, isIntraday);
  const q = isIntraday ? intraday : daily;

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
          Active setups
        </h2>
        <div
          role="tablist"
          aria-label="Scan interval"
          className="inline-flex rounded-md border border-border bg-bg-subtle p-0.5"
        >
          {SCAN_INTERVALS.map((i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={scanInterval === i}
              onClick={() => setScanInterval(i)}
              className={cn(
                "rounded-sm px-2 py-0.5 font-mono text-[10px]",
                scanInterval === i
                  ? "bg-accent text-accent-fg"
                  : "text-fg-muted hover:bg-bg-muted hover:text-fg-base",
              )}
            >
              {i}
            </button>
          ))}
        </div>
      </div>

      {q.isLoading || q.isFetching ? (
        <p className="text-sm text-fg-muted">Scanning…</p>
      ) : !q.data || q.data.length === 0 ? (
        <p className="text-sm text-fg-muted">
          No high-probability setups right now (probability ≥ 60%, R:R ≥ 2).
        </p>
      ) : (
        <ul className="space-y-1.5">
          {q.data.map((a) => (
            <AlertRow key={`${a.symbol}-${a.interval}-${a.setup}`} alert={a} onPick={onPick} />
          ))}
        </ul>
      )}
    </section>
  );
}

function AlertRow({ alert, onPick }: { alert: WaveAlert; onPick: (s: string) => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onPick(alert.symbol)}
        className="flex w-full items-center justify-between gap-3 rounded-md border border-border bg-bg-subtle px-3 py-2 text-left hover:bg-bg-muted"
      >
        <span className="flex items-center gap-2">
          <span className="font-mono text-sm font-medium text-fg-base">{alert.symbol}</span>
          <span
            className={cn(
              "rounded-sm px-1.5 py-0.5 text-[10px] uppercase",
              alert.direction === "long" ? "bg-up/15 text-up" : "bg-down/15 text-down",
            )}
          >
            {alert.direction} · w{alert.current_wave}
          </span>
          <span className="text-xs text-fg-subtle">{alert.trade_type}</span>
        </span>
        <span className="flex items-center gap-3 font-mono text-xs text-fg-muted">
          <span>R:R {alert.risk_reward}</span>
          <span>P {(alert.probability * 100).toFixed(0)}%</span>
          <span className="text-fg-subtle">
            {fmtPrice(alert.entry)}→{fmtPrice(alert.target_1)}
          </span>
        </span>
      </button>
    </li>
  );
}
