import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  useCalendar,
  type CalendarAssetClass,
  type CalendarDay,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function isoUTC(d: Date): string {
  return d.toISOString().slice(0, 10);
}
function addDaysUTC(d: Date, n: number): Date {
  const x = new Date(d);
  x.setUTCDate(x.getUTCDate() + n);
  return x;
}

/**
 * Market Calendar — month grid of trading sessions (open / closed /
 * early-close) for equities or futures, from /api/v1/calendar. Closed days
 * show the holiday/weekend reason; early closes show the ET close time.
 * Event markers (FOMC etc.) render from each day's `events[]` — empty until
 * the events layer lands (docs/market_calendar_spec.md).
 */
export function CalendarPage() {
  const today = useMemo(() => new Date(), []);
  const [assetClass, setAssetClass] = useState<CalendarAssetClass>("equities");
  const [cursor, setCursor] = useState(
    () => new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1)),
  );

  const year = cursor.getUTCFullYear();
  const month = cursor.getUTCMonth();

  // Pad to full weeks: Sunday on/before the 1st → Saturday on/after the last.
  const firstOfMonth = new Date(Date.UTC(year, month, 1));
  const lastOfMonth = new Date(Date.UTC(year, month + 1, 0));
  const gridStart = addDaysUTC(firstOfMonth, -firstOfMonth.getUTCDay());
  const gridEnd = addDaysUTC(lastOfMonth, 6 - lastOfMonth.getUTCDay());

  const { data, isLoading, error } = useCalendar(
    assetClass,
    isoUTC(gridStart),
    isoUTC(gridEnd),
  );

  const byDate = useMemo(() => {
    const m = new Map<string, CalendarDay>();
    for (const d of data?.days ?? []) m.set(d.date, d);
    return m;
  }, [data]);

  const cells = useMemo(() => {
    const out: string[] = [];
    for (let d = gridStart; d <= gridEnd; d = addDaysUTC(d, 1)) {
      out.push(isoUTC(d));
    }
    return out;
  }, [gridStart, gridEnd]);

  const todayIso = isoUTC(new Date(Date.UTC(
    today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(),
  )));

  return (
    <div className="mx-auto max-w-5xl p-4">
      {/* Header */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold">{MONTHS[month]} {year}</h1>
          <Button
            variant="ghost" size="icon" aria-label="Previous month"
            onClick={() => setCursor(new Date(Date.UTC(year, month - 1, 1)))}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost" size="icon" aria-label="Next month"
            onClick={() => setCursor(new Date(Date.UTC(year, month + 1, 1)))}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant="outline" size="sm"
            onClick={() => setCursor(new Date(Date.UTC(
              today.getUTCFullYear(), today.getUTCMonth(), 1,
            )))}
          >
            Today
          </Button>
        </div>

        {/* equities / futures toggle */}
        <div role="tablist" aria-label="Asset class" className="flex rounded-md border border-border">
          {(["equities", "futures"] as const).map((ac) => (
            <button
              key={ac}
              role="tab"
              aria-selected={assetClass === ac}
              onClick={() => setAssetClass(ac)}
              className={cn(
                "px-3 py-1 text-sm capitalize transition-colors first:rounded-l-md last:rounded-r-md",
                assetClass === ac
                  ? "bg-accent text-accent-fg"
                  : "text-fg-muted hover:bg-bg-subtle",
              )}
            >
              {ac}
            </button>
          ))}
        </div>
      </div>

      {error ? <ApiErrorAlert error={error} /> : null}

      {/* Weekday header */}
      <div className="grid grid-cols-7 gap-px text-center text-xs font-medium text-fg-muted">
        {WEEKDAYS.map((w) => (
          <div key={w} className="py-1">{w}</div>
        ))}
      </div>

      {/* Day grid */}
      <div
        className={cn(
          "grid grid-cols-7 gap-px overflow-hidden rounded-md border border-border bg-border",
          isLoading && "opacity-60",
        )}
      >
        {cells.map((iso) => {
          const day = byDate.get(iso);
          const inMonth = Number(iso.slice(5, 7)) === month + 1;
          const dayNum = Number(iso.slice(8, 10));
          const status = day?.status ?? "open";
          const isToday = iso === todayIso;
          return (
            <div
              key={iso}
              className={cn(
                "min-h-[84px] bg-bg p-1.5 text-sm",
                !inMonth && "opacity-40",
                status === "closed" && "bg-bg-subtle",
                isToday && "ring-2 ring-inset ring-accent",
              )}
            >
              <div className="flex items-center justify-between">
                <span className={cn("font-medium", isToday && "text-accent")}>
                  {dayNum}
                </span>
                {status === "early_close" ? (
                  <span className="rounded bg-warning/15 px-1 text-[10px] font-medium text-warning">
                    ½ · {day?.early_close_et} ET
                  </span>
                ) : null}
              </div>

              {status === "closed" ? (
                <div className="mt-1 text-[11px] leading-tight text-fg-muted">
                  {day?.reason === "Weekend" ? "" : (day?.reason ?? "Closed")}
                </div>
              ) : null}

              {/* event chips (FOMC, OPEX, …) */}
              {day?.events?.length ? (
                <div className="mt-1 flex flex-col gap-0.5">
                  {day.events.slice(0, 3).map((e, i) => (
                    <span
                      key={i}
                      title={`${e.title}${e.time_et ? ` · ${e.time_et} ET` : ""}`}
                      className={cn(
                        "truncate rounded px-1 text-[10px] font-medium leading-tight",
                        e.importance === "high"
                          ? "bg-accent/20 text-accent"
                          : "bg-fg-muted/15 text-fg-muted",
                      )}
                    >
                      {e.symbol ? `${e.symbol} ` : ""}{e.title}
                    </span>
                  ))}
                  {day.events.length > 3 ? (
                    <span className="px-1 text-[10px] text-fg-muted">
                      +{day.events.length - 3} more
                    </span>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-fg-muted">
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded border border-border bg-bg" /> Open
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded border border-border bg-bg-subtle" /> Closed
        </span>
        <span className="flex items-center gap-1">
          <span className="rounded bg-warning/15 px-1 text-[10px] text-warning">½</span> Early close
        </span>
      </div>
    </div>
  );
}
