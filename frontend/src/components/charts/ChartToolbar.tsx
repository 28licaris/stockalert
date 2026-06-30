import { useEffect, useRef, useState } from "react";
import {
  AreaChart as AreaChartIcon,
  CandlestickChart,
  ChevronDown,
  LineChart as LineChartIcon,
  Plus,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { TZ_OPTIONS, type TzSetting } from "@/lib/timezone";
import { CHART_RANGES, type ChartRange } from "@/api/queries";
import {
  INDICATOR_CATALOG,
  indicatorChipLabel,
  type IndicatorKind,
} from "./indicatorCatalog";
import type { ChartType } from "./OhlcvChart";
import {
  isCrossTimeframeMA,
  sourceOptions,
  type MovingAverageKind,
  type MovingAverageOverlay,
} from "./movingAverage";

export type { MovingAverageKind, MovingAverageOverlay } from "./movingAverage";

interface ChartToolbarProps {
  interval: string;
  intervals: readonly string[];
  onIntervalChange: (i: string) => void;
  range: ChartRange;
  onRangeChange: (range: ChartRange) => void;
  chartType: ChartType;
  onChartTypeChange: (t: ChartType) => void;
  tz: TzSetting;
  onTzChange: (t: TzSetting) => void;
  /** Selected indicator ids (registry names). */
  selected: ReadonlyArray<string>;
  onToggleIndicator: (id: string) => void;
  onClearIndicators: () => void;
  indicatorSettings: Record<string, Record<string, number>>;
  onIndicatorSettingChange: (id: string, key: string, value: number) => void;
  movingAverages: ReadonlyArray<MovingAverageOverlay>;
  onAddMovingAverage: (kind: MovingAverageKind) => void;
  onUpdateMovingAverage: (
    id: string,
    patch: Partial<Omit<MovingAverageOverlay, "id">>,
  ) => void;
  onRemoveMovingAverage: (id: string) => void;
  onClearMovingAverages: () => void;
}

/** Shared styling for the segmented control groups. */
const GROUP = "inline-flex rounded-md border border-border bg-bg-base/55 p-1";
const SEG_BASE = "rounded px-2.5 py-1 font-mono text-xs transition-colors";
const SEG_ON = "bg-accent text-accent-fg shadow-[0_0_22px_rgba(46,196,255,0.14)]";
const SEG_OFF = "text-fg-muted hover:bg-bg-muted/70 hover:text-fg-base";

/**
 * Chart control bar — chart type, interval, timezone, and the indicator
 * picker, grouped into segmented controls. Selected indicators surface
 * as removable chips below the controls.
 */
export function ChartToolbar({
  interval,
  intervals,
  onIntervalChange,
  range,
  onRangeChange,
  chartType,
  onChartTypeChange,
  tz,
  onTzChange,
  selected,
  onToggleIndicator,
  onClearIndicators,
  indicatorSettings,
  onIndicatorSettingChange,
  movingAverages,
  onAddMovingAverage,
  onUpdateMovingAverage,
  onRemoveMovingAverage,
  onClearMovingAverages,
}: ChartToolbarProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <ChartTypeToggle value={chartType} onChange={onChartTypeChange} />

        <div role="tablist" aria-label="Interval" className={GROUP}>
          {intervals.map((i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={interval === i}
              onClick={() => onIntervalChange(i)}
              className={cn(SEG_BASE, interval === i ? SEG_ON : SEG_OFF)}
            >
              {i}
            </button>
          ))}
        </div>

        <div role="tablist" aria-label="Range" className={GROUP}>
          {CHART_RANGES.map((r) => (
            <button
              key={r}
              type="button"
              role="tab"
              aria-selected={range === r}
              onClick={() => onRangeChange(r)}
              className={cn(SEG_BASE, range === r ? SEG_ON : SEG_OFF)}
            >
              {r}
            </button>
          ))}
        </div>

        <div role="tablist" aria-label="Timezone" className={GROUP}>
          {TZ_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              role="tab"
              aria-selected={tz === opt.value}
              onClick={() => onTzChange(opt.value)}
              className={cn(SEG_BASE, tz === opt.value ? SEG_ON : SEG_OFF)}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <IndicatorMenu
          interval={interval}
          selected={selected}
          settings={indicatorSettings}
          movingAverages={movingAverages}
          onToggle={onToggleIndicator}
          onSettingChange={onIndicatorSettingChange}
          onAddMovingAverage={onAddMovingAverage}
          onUpdateMovingAverage={onUpdateMovingAverage}
          onRemoveMovingAverage={onRemoveMovingAverage}
        />
      </div>

      {selected.length > 0 || movingAverages.length > 0 ? (
        <IndicatorChips
          interval={interval}
          selected={selected}
          settings={indicatorSettings}
          movingAverages={movingAverages}
          onRemove={onToggleIndicator}
          onRemoveMovingAverage={onRemoveMovingAverage}
          onClear={onClearIndicators}
          onClearMovingAverages={onClearMovingAverages}
        />
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

const CHART_TYPES: ReadonlyArray<{
  id: ChartType;
  label: string;
  Icon: typeof CandlestickChart;
}> = [
  { id: "candles", label: "Candlesticks", Icon: CandlestickChart },
  { id: "line", label: "Line", Icon: LineChartIcon },
  { id: "area", label: "Area", Icon: AreaChartIcon },
];

function ChartTypeToggle({
  value,
  onChange,
}: {
  value: ChartType;
  onChange: (t: ChartType) => void;
}) {
  return (
    <div role="tablist" aria-label="Chart type" className={GROUP}>
      {CHART_TYPES.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={value === id}
          title={label}
          aria-label={label}
          onClick={() => onChange(id)}
          className={cn(
            SEG_BASE,
            "flex items-center",
            value === id ? SEG_ON : SEG_OFF,
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden />
        </button>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

const KIND_LABEL: Record<IndicatorKind, string> = {
  overlay: "Overlays",
  oscillator: "Oscillators",
};

function IndicatorMenu({
  interval,
  selected,
  settings,
  movingAverages,
  onToggle,
  onSettingChange,
  onAddMovingAverage,
  onUpdateMovingAverage,
  onRemoveMovingAverage,
}: {
  interval: string;
  selected: ReadonlyArray<string>;
  settings: Record<string, Record<string, number>>;
  movingAverages: ReadonlyArray<MovingAverageOverlay>;
  onToggle: (id: string) => void;
  onSettingChange: (id: string, key: string, value: number) => void;
  onAddMovingAverage: (kind: MovingAverageKind) => void;
  onUpdateMovingAverage: (
    id: string,
    patch: Partial<Omit<MovingAverageOverlay, "id">>,
  ) => void;
  onRemoveMovingAverage: (id: string) => void;
}) {
  const srcOptions = sourceOptions(interval);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selectedSet = new Set(selected);
  const kinds: IndicatorKind[] = ["overlay", "oscillator"];

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          "inline-flex items-center gap-1 rounded-md border border-border bg-bg-base/55 px-2.5 py-1.5 text-xs text-fg-muted transition-colors hover:border-accent/40 hover:bg-bg-muted/70 hover:text-fg-base",
          open && "border-accent/40 bg-bg-muted text-fg-base",
        )}
      >
        <Plus className="h-3.5 w-3.5" aria-hidden />
        <span>Indicators</span>
        {selected.length + movingAverages.length > 0 ? (
          <span className="ml-0.5 rounded-full bg-accent px-1.5 font-mono text-[10px] text-accent-fg">
            {selected.length + movingAverages.length}
          </span>
        ) : null}
        <ChevronDown className="h-3 w-3 opacity-60" aria-hidden />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-80 rounded-md border border-border bg-bg-elevated p-2 shadow-2xl shadow-black/40"
        >
          <div className="border-b border-border/70 pb-2">
            <div className="flex items-center justify-between gap-2 px-1 py-1">
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
                  Moving averages
                </div>
                <div className="text-[10px] text-fg-muted">
                  Length counts source bars. Pin a source to lock a level
                  (e.g. 200 · 1d = a true 200-day SMA on any chart).
                </div>
              </div>
              <div className="flex gap-1">
                {MA_KINDS.map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    onClick={() => onAddMovingAverage(kind)}
                    className="rounded border border-border bg-bg-base px-2 py-1 font-mono text-[10px] uppercase text-fg-muted hover:border-accent/50 hover:text-fg-base"
                  >
                    + {kind}
                  </button>
                ))}
              </div>
            </div>
            {movingAverages.length > 0 ? (
              <div className="mt-1 space-y-1">
                {movingAverages.map((ma) => (
                  <div
                    key={ma.id}
                    className="space-y-1 rounded border border-border/70 bg-bg-base/50 p-1"
                  >
                    <div className="flex items-center gap-1">
                      <select
                        value={ma.kind}
                        aria-label="Type"
                        onChange={(event) =>
                          onUpdateMovingAverage(ma.id, {
                            kind: event.target.value as MovingAverageKind,
                          })
                        }
                        className="h-7 flex-1 rounded border border-border bg-bg-base px-1 font-mono text-[11px] uppercase text-fg-base focus:border-accent focus:outline-none"
                      >
                        {MA_KINDS.map((kind) => (
                          <option key={kind} value={kind}>
                            {kind.toUpperCase()}
                          </option>
                        ))}
                      </select>
                      <label className="flex items-center gap-1 text-[10px] text-fg-muted">
                        <span>Source</span>
                        <select
                          value={ma.sourceAgg ?? "chart"}
                          aria-label="Source timeframe"
                          onChange={(event) =>
                            onUpdateMovingAverage(ma.id, {
                              sourceAgg:
                                event.target.value === "chart"
                                  ? undefined
                                  : event.target.value,
                            })
                          }
                          className="h-7 rounded border border-border bg-bg-base px-1 font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none"
                        >
                          {srcOptions.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        aria-label={`Remove ${ma.kind.toUpperCase()} ${ma.period}`}
                        onClick={() => onRemoveMovingAverage(ma.id)}
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-fg-subtle hover:bg-bg-muted hover:text-fg-base"
                      >
                        <X className="h-3 w-3" aria-hidden />
                      </button>
                    </div>
                    <div className="flex items-center gap-1">
                      <label className="flex flex-1 items-center gap-1 text-[10px] text-fg-muted">
                        <span>Length</span>
                        <input
                          type="number"
                          min={1}
                          max={1000}
                          step={1}
                          value={ma.period}
                          onChange={(event) => {
                            const next = Number(event.target.value);
                            if (Number.isFinite(next)) {
                              onUpdateMovingAverage(ma.id, {
                                period: Math.max(1, Math.round(next)),
                              });
                            }
                          }}
                          className="h-7 min-w-0 flex-1 rounded border border-border bg-bg-base px-2 text-right font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none"
                        />
                      </label>
                      <input
                        type="color"
                        value={ma.color}
                        aria-label={`${ma.kind.toUpperCase()} ${ma.period} color`}
                        onChange={(event) =>
                          onUpdateMovingAverage(ma.id, { color: event.target.value })
                        }
                        className="h-7 w-9 rounded border border-border bg-bg-base p-0.5"
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
          {kinds.map((kind) => (
            <div key={kind} className="py-1">
              <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
                {KIND_LABEL[kind]}
              </div>
              {INDICATOR_CATALOG.filter((d) => d.kind === kind).map((d) => {
                const on = selectedSet.has(d.id);
                return (
                  <div key={d.id}>
                    <button
                      type="button"
                      role="menuitemcheckbox"
                      aria-checked={on}
                      onClick={() => onToggle(d.id)}
                      className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs text-fg-base hover:bg-bg-muted"
                    >
                      <span
                        className={cn(
                          "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border",
                          on
                            ? "border-accent bg-accent text-accent-fg"
                            : "border-border",
                        )}
                        aria-hidden
                      >
                        {on ? "✓" : ""}
                      </span>
                      <span className="flex flex-col">
                        <span className="font-medium">{d.label}</span>
                        <span className="text-[10px] text-fg-subtle">
                          {d.description}
                        </span>
                      </span>
                    </button>
                    {on && supportsPeriodSetting(d.id) ? (
                      <label className="mx-2 mb-1 flex items-center justify-between gap-3 rounded bg-bg-base/50 px-2 py-1.5 text-[11px] text-fg-muted">
                        <span>Period</span>
                        <input
                          type="number"
                          min={1}
                          max={500}
                          step={1}
                          value={settings[d.id]?.period ?? 20}
                          onChange={(event) => {
                            const next = Number(event.target.value);
                            if (Number.isFinite(next)) {
                              onSettingChange(
                                d.id,
                                "period",
                                Math.max(1, Math.round(next)),
                              );
                            }
                          }}
                          className="h-7 w-20 rounded border border-border bg-bg-base px-2 text-right font-mono text-xs text-fg-base focus:border-accent focus:outline-none"
                        />
                      </label>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function supportsPeriodSetting(id: string): boolean {
  return id === "sma" || id === "ema" || id === "wma";
}

const MA_KINDS: MovingAverageKind[] = ["sma", "ema", "wma"];

// ─────────────────────────────────────────────────────────────────────

function IndicatorChips({
  interval,
  selected,
  settings,
  movingAverages,
  onRemove,
  onRemoveMovingAverage,
  onClear,
  onClearMovingAverages,
}: {
  interval: string;
  selected: ReadonlyArray<string>;
  settings: Record<string, Record<string, number>>;
  movingAverages: ReadonlyArray<MovingAverageOverlay>;
  onRemove: (id: string) => void;
  onRemoveMovingAverage: (id: string) => void;
  onClear: () => void;
  onClearMovingAverages: () => void;
}) {
  const maSuffix = interval === "1d" ? "D" : `×${interval}`;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {movingAverages.map((ma) => {
        const crossTf = isCrossTimeframeMA(ma, interval);
        return (
        <span
          key={ma.id}
          className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-subtle py-0.5 pl-2 pr-1 font-mono text-[11px] text-fg-base"
          title={
            crossTf
              ? `${ma.kind.toUpperCase()} ${ma.period} on ${ma.sourceAgg} bars, stepped onto the ${interval} chart`
              : `${ma.kind.toUpperCase()} ${ma.period} on ${interval} bars`
          }
        >
          <span
            className="h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: ma.color }}
            aria-hidden
          />
          {ma.kind.toUpperCase()}
          <span className="text-[10px] text-fg-subtle">
            {ma.period}
            {crossTf ? ` · ${ma.sourceAgg}` : maSuffix}
          </span>
          <button
            type="button"
            aria-label={`Remove ${ma.kind.toUpperCase()} ${ma.period}`}
            onClick={() => onRemoveMovingAverage(ma.id)}
            className="flex h-3.5 w-3.5 items-center justify-center rounded-full text-fg-subtle hover:bg-bg-muted hover:text-fg-base"
          >
            <X className="h-2.5 w-2.5" aria-hidden />
          </button>
        </span>
        );
      })}
      {selected.map((id) => {
        const period = supportsPeriodSetting(id) ? settings[id]?.period : null;
        return (
          <span
            key={id}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-subtle py-0.5 pl-2 pr-1 font-mono text-[11px] text-fg-base"
          >
            {indicatorChipLabel(id)}
            {period ? (
              <span className="text-[10px] text-fg-subtle">{period}</span>
            ) : null}
            <button
              type="button"
              aria-label={`Remove ${indicatorChipLabel(id)}`}
              onClick={() => onRemove(id)}
              className="flex h-3.5 w-3.5 items-center justify-center rounded-full text-fg-subtle hover:bg-bg-muted hover:text-fg-base"
            >
              <X className="h-2.5 w-2.5" aria-hidden />
            </button>
          </span>
        );
      })}
      {selected.length + movingAverages.length > 1 ? (
        <button
          type="button"
          onClick={() => {
            onClear();
            onClearMovingAverages();
          }}
          className="ml-1 text-[11px] text-fg-subtle hover:text-fg-base"
        >
          Clear all
        </button>
      ) : null}
    </div>
  );
}
