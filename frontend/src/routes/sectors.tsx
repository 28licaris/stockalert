import { useMemo, useState } from "react";
import { ChevronDown, Layers } from "lucide-react";
import {
  useSectorRotation,
  type RotationDashboard,
  type RotationQuadrant,
  type SectorRotationState,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { cn } from "@/lib/utils";

/**
 * Sector Rotation (RRG) — where the 11 S&P sectors sit vs SPY on two axes:
 * relative strength (RS-Ratio) and its momentum (RS-Momentum), which sort
 * each into Leading / Weakening / Improving / Lagging. Clean dots by default;
 * focus a sector (hover/click) to trace its path. The rotation table below
 * shows each sector's quadrant journey week-by-week. /api/v1/sectors/rotation.
 */

type QuadMeta = { label: string; color: string; soft: string; badge: string };

const QUADRANTS: Record<RotationQuadrant, QuadMeta> = {
  leading: { label: "Leading", color: "#10b981", soft: "rgba(16,185,129,0.10)", badge: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  weakening: { label: "Weakening", color: "#f59e0b", soft: "rgba(245,158,11,0.10)", badge: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  improving: { label: "Improving", color: "#3b82f6", soft: "rgba(59,130,246,0.10)", badge: "bg-blue-500/15 text-blue-300 border-blue-500/30" },
  lagging: { label: "Lagging", color: "#f43f5e", soft: "rgba(244,63,94,0.10)", badge: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
};
const QUAD_ORDER: RotationQuadrant[] = ["leading", "weakening", "improving", "lagging"];

type Pt = { x: number; y: number };

/** Catmull-Rom → cubic bézier, for smooth rotation trails. */
function smoothPath(pts: Pt[]): string {
  if (pts.length < 2) return "";
  const f = (n: number) => n.toFixed(1);
  if (pts.length === 2) return `M${f(pts[0].x)},${f(pts[0].y)} L${f(pts[1].x)},${f(pts[1].y)}`;
  let d = `M${f(pts[0].x)},${f(pts[0].y)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C${f(c1x)},${f(c1y)} ${f(c2x)},${f(c2y)} ${f(p2.x)},${f(p2.y)}`;
  }
  return d;
}

// ─────────────────────────────────────────────────────────────────────
// RRG quadrant scatter — clean dots; trails only for focused sectors.
// ─────────────────────────────────────────────────────────────────────

function RrgScatter({
  sectors,
  active,
  onHover,
  onToggle,
}: {
  sectors: SectorRotationState[];
  active: Set<string>;
  onHover: (id: string | null) => void;
  onToggle: (id: string) => void;
}) {
  const W = 600;
  const H = 600;
  const M = 50;

  const { sx, sy, cx, cy, gx, gy } = useMemo(() => {
    const xs: number[] = [];
    const ys: number[] = [];
    for (const s of sectors) {
      for (const p of [...(s.tail ?? []), s.current]) {
        xs.push(p.rs_ratio);
        ys.push(p.rs_momentum);
      }
    }
    const devX = Math.max(2.5, ...xs.map((v) => Math.abs(v - 100))) * 1.2;
    const devY = Math.max(2.5, ...ys.map((v) => Math.abs(v - 100))) * 1.2;
    const sx = (v: number) => M + ((v - (100 - devX)) / (2 * devX)) * (W - 2 * M);
    const sy = (v: number) => M + ((100 + devY - v) / (2 * devY)) * (H - 2 * M);
    // gridline positions (½ way into each quadrant)
    const gx = [100 - devX / 2, 100 + devX / 2];
    const gy = [100 - devY / 2, 100 + devY / 2];
    return { sx, sy, cx: sx(100), cy: sy(100), gx, gy };
  }, [sectors]);

  // Greedy vertical de-clutter for the dot labels (sectors cluster near 100).
  const labels = useMemo(() => {
    const arr = sectors.map((s) => ({
      id: s.group_id,
      dx: sx(s.current.rs_ratio),
      dy: sy(s.current.rs_momentum),
      color: QUADRANTS[s.current.quadrant].color,
      ly: sy(s.current.rs_momentum),
    }));
    arr.sort((a, b) => a.dy - b.dy);
    let prev = -Infinity;
    for (const l of arr) {
      l.ly = Math.max(l.dy - 3, prev + 13);
      prev = l.ly;
    }
    return arr;
  }, [sectors, sx, sy]);

  const hasActive = active.size > 0;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full select-none" role="img" aria-label="Relative rotation graph">
      <defs>
        {sectors.map((s) => (
          <linearGradient key={s.group_id} id={`tail-${s.group_id}`} gradientUnits="userSpaceOnUse"
            x1={sx((s.tail?.[0] ?? s.current).rs_ratio)} y1={sy((s.tail?.[0] ?? s.current).rs_momentum)}
            x2={sx(s.current.rs_ratio)} y2={sy(s.current.rs_momentum)}>
            <stop offset="0%" stopColor={QUADRANTS[s.current.quadrant].color} stopOpacity="0.05" />
            <stop offset="100%" stopColor={QUADRANTS[s.current.quadrant].color} stopOpacity="0.95" />
          </linearGradient>
        ))}
      </defs>

      {/* quadrant fills */}
      <rect x={cx} y={M} width={W - M - cx} height={cy - M} fill={QUADRANTS.leading.soft} />
      <rect x={cx} y={cy} width={W - M - cx} height={H - M - cy} fill={QUADRANTS.weakening.soft} />
      <rect x={M} y={M} width={cx - M} height={cy - M} fill={QUADRANTS.improving.soft} />
      <rect x={M} y={cy} width={cx - M} height={H - M - cy} fill={QUADRANTS.lagging.soft} />

      {/* faint guides */}
      {gx.map((v, i) => <line key={`gx${i}`} x1={sx(v)} y1={M} x2={sx(v)} y2={H - M} className="stroke-border" strokeOpacity={0.4} />)}
      {gy.map((v, i) => <line key={`gy${i}`} x1={M} y1={sy(v)} x2={W - M} y2={sy(v)} className="stroke-border" strokeOpacity={0.4} />)}

      {/* 100 cross */}
      <line x1={cx} y1={M} x2={cx} y2={H - M} className="stroke-fg-subtle" strokeOpacity={0.5} strokeDasharray="4 4" />
      <line x1={M} y1={cy} x2={W - M} y2={cy} className="stroke-fg-subtle" strokeOpacity={0.5} strokeDasharray="4 4" />

      {/* quadrant labels */}
      <text x={W - M - 10} y={M + 18} textAnchor="end" className="fill-emerald-400/80" fontSize={13} fontWeight={700} letterSpacing="0.08em">LEADING</text>
      <text x={W - M - 10} y={H - M - 10} textAnchor="end" className="fill-amber-400/80" fontSize={13} fontWeight={700} letterSpacing="0.08em">WEAKENING</text>
      <text x={M + 10} y={M + 18} className="fill-blue-400/80" fontSize={13} fontWeight={700} letterSpacing="0.08em">IMPROVING</text>
      <text x={M + 10} y={H - M - 10} className="fill-rose-400/80" fontSize={13} fontWeight={700} letterSpacing="0.08em">LAGGING</text>
      <text x={cx} y={H - M + 16} textAnchor="middle" className="fill-fg-subtle" fontSize={11}>RS-Ratio →</text>
      <text x={M - 16} y={cy} textAnchor="middle" className="fill-fg-subtle" fontSize={11} transform={`rotate(-90 ${M - 16} ${cy})`}>RS-Momentum →</text>

      {/* focused trails */}
      {sectors.map((s) => {
        if (!active.has(s.group_id)) return null;
        const tail = [...(s.tail ?? []), s.current];
        const pts = tail.map((p) => ({ x: sx(p.rs_ratio), y: sy(p.rs_momentum) }));
        const meta = QUADRANTS[s.current.quadrant];
        return (
          <g key={`trail-${s.group_id}`} className="pointer-events-none">
            <path d={smoothPath(pts)} fill="none" stroke={`url(#tail-${s.group_id})`} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
            {pts.slice(0, -1).map((p, i) => <circle key={i} cx={p.x} cy={p.y} r={2} fill={meta.color} opacity={0.25 + 0.6 * (i / pts.length)} />)}
          </g>
        );
      })}

      {/* current dots */}
      {sectors.map((s) => {
        const meta = QUADRANTS[s.current.quadrant];
        const on = active.has(s.group_id);
        const dim = hasActive && !on;
        return (
          <g key={s.group_id} className="cursor-pointer" opacity={dim ? 0.28 : 1}
            onMouseEnter={() => onHover(s.group_id)} onMouseLeave={() => onHover(null)}
            onClick={() => onToggle(s.group_id)}>
            {on && <circle cx={sx(s.current.rs_ratio)} cy={sy(s.current.rs_momentum)} r={11} fill={meta.color} opacity={0.18} />}
            <circle cx={sx(s.current.rs_ratio)} cy={sy(s.current.rs_momentum)} r={on ? 6.5 : 5} fill={meta.color} className="stroke-bg-base" strokeWidth={2} />
          </g>
        );
      })}

      {/* de-cluttered labels with leader lines */}
      {labels.map((l) => {
        const dim = hasActive && !active.has(l.id);
        const lx = l.dx + 9;
        return (
          <g key={`lbl-${l.id}`} opacity={dim ? 0.28 : 1} className="pointer-events-none">
            {Math.abs(l.ly - l.dy) > 5 && <line x1={l.dx + 5} y1={l.dy} x2={lx - 1} y2={l.ly - 3} stroke={l.color} strokeOpacity={0.35} strokeWidth={1} />}
            <text x={lx} y={l.ly} fontSize={12} fontWeight={active.has(l.id) ? 700 : 600} fill={l.color}>{l.id}</text>
          </g>
        );
      })}
    </svg>
  );
}

// ─────────────────────────────────────────────────────────────────────

function QuadrantBadge({ quadrant }: { quadrant: RotationQuadrant }) {
  const meta = QUADRANTS[quadrant];
  return <span className={cn("inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium", meta.badge)}>{meta.label}</span>;
}

function QuadrantSummary({ sectors }: { sectors: SectorRotationState[] }) {
  const counts = useMemo(() => {
    const m: Record<RotationQuadrant, number> = { leading: 0, weakening: 0, improving: 0, lagging: 0 };
    for (const s of sectors) m[s.current.quadrant] += 1;
    return m;
  }, [sectors]);
  return (
    <div className="grid grid-cols-4 gap-2">
      {QUAD_ORDER.map((q) => (
        <div key={q} className="rounded-lg border border-border bg-bg-subtle px-2 py-2.5 text-center">
          <div className="text-2xl font-semibold leading-none" style={{ color: QUADRANTS[q].color }}>{counts[q]}</div>
          <div className="mt-1 text-[11px] text-fg-subtle">{QUADRANTS[q].label}</div>
        </div>
      ))}
    </div>
  );
}

/** A plain-language read of the board — the "so what". */
function MarketRead({ sectors }: { sectors: SectorRotationState[] }) {
  const by = (q: RotationQuadrant) =>
    sectors.filter((s) => s.current.quadrant === q).sort((a, b) => b.current.rs_ratio - a.current.rs_ratio).map((s) => s.group_id);
  const rows: { q: RotationQuadrant; ids: string[] }[] = QUAD_ORDER.map((q) => ({ q, ids: by(q) })).filter((r) => r.ids.length);
  return (
    <div className="rounded-lg border border-border bg-bg-subtle p-3">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">Market read</div>
      <div className="flex flex-col gap-1.5">
        {rows.map(({ q, ids }) => (
          <div key={q} className="flex items-baseline gap-2 text-sm">
            <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: QUADRANTS[q].color }} />
            <span className="w-20 shrink-0 text-fg-muted">{QUADRANTS[q].label}</span>
            <span className="font-mono text-xs text-fg-base">{ids.join("  ")}</span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-fg-subtle">
        Rotation runs clockwise: Improving → Leading → Weakening → Lagging.
      </p>
    </div>
  );
}

/** One-line relative-strength sparkline vs SPY (rebased to 100). */
function MiniRs({ points, color }: { points: [string, number][]; color: string }) {
  const W = 96;
  const H = 26;
  if (points.length < 2) return <svg viewBox={`0 0 ${W} ${H}`} className="h-6 w-24" />;
  const vals = points.map((p) => p[1]);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const y = (v: number) => H - 2 - ((v - min) / span) * (H - 4);
  const d = vals.map((v, i) => `${i === 0 ? "M" : "L"}${((i / (vals.length - 1)) * W).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const y100 = 100 >= min && 100 <= max ? y(100) : null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-6 w-24" preserveAspectRatio="none">
      {y100 !== null && <line x1={0} y1={y100} x2={W} y2={y100} className="stroke-fg-subtle" strokeOpacity={0.3} strokeDasharray="2 2" />}
      <path d={d} fill="none" stroke={color} strokeWidth={1.25} />
    </svg>
  );
}

/** The rotation table — quadrant journey + RS sparkline per sector. */
function RotationTable({
  sectors,
  active,
  onHover,
  onToggle,
}: {
  sectors: SectorRotationState[];
  active: Set<string>;
  onHover: (id: string | null) => void;
  onToggle: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleExpand = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const maxCells = Math.max(1, ...sectors.map((s) => s.tail?.length ?? 0));
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-3 border-b border-border bg-bg-subtle px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
        <span className="w-40 shrink-0">Sector</span>
        <span className="flex-1">12-week rotation <span className="text-fg-subtle/60">(older → now)</span></span>
        <span className="hidden w-24 text-right sm:block">RS vs SPY</span>
        <span className="w-32 text-right">Now</span>
      </div>
      {sectors.map((s) => {
        const on = active.has(s.group_id);
        const dim = active.size > 0 && !on;
        const tail = s.tail ?? [];
        const pad = maxCells - tail.length;
        const meta = QUADRANTS[s.current.quadrant];
        const isBasket = s.kind === "basket";
        const members = s.members ?? [];
        const open = expanded.has(s.group_id);
        return (
          <div key={s.group_id} className="border-b border-border/40 last:border-0">
            <div role="button" tabIndex={0}
              onMouseEnter={() => onHover(s.group_id)} onMouseLeave={() => onHover(null)}
              onClick={() => onToggle(s.group_id)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onToggle(s.group_id)}
              className={cn("flex w-full cursor-pointer items-center gap-3 px-3 py-2 text-left transition-colors",
                on ? "bg-bg-muted" : "hover:bg-bg-subtle", dim && "opacity-45")}>
              <span className="flex w-40 shrink-0 flex-col">
                <span className="flex items-baseline gap-2">
                  <span className="font-mono text-sm font-semibold text-fg-base">{s.group_id}</span>
                  <span className="truncate text-xs text-fg-subtle">{s.name}</span>
                </span>
                {isBasket && (
                  <button type="button"
                    onClick={(e) => { e.stopPropagation(); toggleExpand(s.group_id); }}
                    className="mt-0.5 flex w-fit items-center gap-1 rounded text-[11px] text-accent hover:underline">
                    <Layers className="h-3 w-3" />
                    {members.length} holdings
                    <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
                  </button>
                )}
              </span>
              <span className="flex flex-1 items-center gap-[3px]">
                {Array.from({ length: pad }).map((_, i) => <span key={`p${i}`} className="h-3.5 w-3.5 rounded-sm bg-bg-muted/40" />)}
                {tail.map((p, i) => (
                  <span key={i} title={`${p.date} · ${QUADRANTS[p.quadrant].label}`} className="h-3.5 w-3.5 rounded-sm"
                    style={{ background: QUADRANTS[p.quadrant].color, opacity: 0.3 + 0.7 * (tail.length > 1 ? i / (tail.length - 1) : 1) }} />
                ))}
              </span>
              <span className="hidden w-24 justify-end sm:flex"><MiniRs points={(s.relative_strength ?? []) as [string, number][]} color={meta.color} /></span>
              <span className="flex w-32 items-center justify-end gap-2">
                <span className="tabular-nums text-xs text-fg-subtle">{s.current.rs_ratio.toFixed(1)}</span>
                <QuadrantBadge quadrant={s.current.quadrant} />
              </span>
            </div>
            {isBasket && open && (
              <div className="flex flex-wrap gap-1.5 bg-bg-base/60 px-3 pb-2.5 pl-3">
                <span className="mr-1 text-[11px] text-fg-subtle">Holdings (equal weight):</span>
                {members.map((m) => (
                  <span key={m} className="rounded border border-border bg-bg-subtle px-1.5 py-0.5 font-mono text-[11px] text-fg-muted">{m}</span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function SectorsPage() {
  const { data, isLoading, error } = useSectorRotation();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [hovered, setHovered] = useState<string | null>(null);

  const dash = data as RotationDashboard | undefined;
  const sectors = useMemo<SectorRotationState[]>(() => {
    const list = dash?.sectors ?? [];
    const rank = (q: RotationQuadrant) => QUAD_ORDER.indexOf(q);
    return [...list].sort((a, b) => rank(a.current.quadrant) - rank(b.current.quadrant) || b.current.rs_ratio - a.current.rs_ratio);
  }, [dash]);

  const active = useMemo(() => {
    const s = new Set(selected);
    if (hovered) s.add(hovered);
    return s;
  }, [selected, hovered]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="mx-auto max-w-6xl p-4">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-fg-base">Sector Rotation</h1>
          <p className="text-sm text-fg-muted">S&amp;P sectors &amp; themes vs {dash?.benchmark ?? "SPY"} — relative strength &amp; momentum (RRG).</p>
        </div>
        {dash && (
          <div className="text-right text-xs text-fg-subtle">
            <div>as of {dash.as_of}</div>
            <div>{dash.tail_weeks}-week tails</div>
          </div>
        )}
      </header>

      {error && <ApiErrorAlert error={error} />}
      {isLoading && <div className="py-24 text-center text-fg-muted">Loading rotation…</div>}

      {dash && !isLoading && (
        <>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.5fr_1fr]">
            <div className="rounded-lg border border-border bg-bg-base p-2">
              <RrgScatter sectors={sectors} active={active} onHover={setHovered} onToggle={toggle} />
              <p className="px-2 pb-1 text-center text-[11px] text-fg-subtle">
                {active.size ? "Showing focused trail — click to pin / unpin." : "Hover or click a sector to trace its 12-week path."}
              </p>
            </div>
            <div className="flex flex-col gap-3">
              <QuadrantSummary sectors={sectors} />
              <MarketRead sectors={sectors} />
            </div>
          </div>

          <div className="mt-4">
            <RotationTable sectors={sectors} active={active} onHover={setHovered} onToggle={toggle} />
          </div>

          {dash.excluded && dash.excluded.length > 0 && (
            <p className="mt-2 text-xs text-fg-subtle">Excluded: {dash.excluded.map((e) => `${e.group_id} (${e.reason})`).join(", ")}</p>
          )}
        </>
      )}
    </div>
  );
}
