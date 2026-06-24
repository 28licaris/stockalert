import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

export function EwtGuidePage() {
  return (
    <div className="mx-auto max-w-3xl space-y-10 px-4 py-8 md:px-6">
      <header className="space-y-1">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-bold tracking-tight text-fg-base">
            Elliott Wave Quick Guide
          </h1>
          <Link
            to="/ewt"
            className="ml-auto rounded-md border border-border px-3 py-1 text-xs text-fg-muted hover:bg-bg-muted"
          >
            ← Back to EWT
          </Link>
        </div>
        <p className="text-sm text-fg-subtle">
          Ralph Nelson Elliott (1938) observed that market prices move in repetitive fractal
          wave patterns — 5 waves with the trend, 3 waves against it. Every timeframe nests
          inside a larger degree: a 5-minute impulse is a single wave inside an hourly
          structure that is itself one wave inside a daily count.
        </p>
      </header>

      {/* ── 1. Three Hard Rules ─────────────────────────────────────────── */}
      <Section
        title="1 · The Three Hard Rules"
        subtitle="Break any of these and the count is invalid — discard it."
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <RuleCard
            number="Rule 1"
            title="Wave 2 never fully retraces Wave 1"
            body="Wave 2 cannot end at or below the origin of Wave 1. If it does, what you labeled Wave 1 is not Wave 1."
            color="up"
          />
          <RuleCard
            number="Rule 2"
            title="Wave 3 is never the shortest motive wave"
            body="Among Waves 1, 3, and 5, Wave 3 cannot be the shortest in price length. It is usually the longest and most powerful."
            color="accent"
          />
          <RuleCard
            number="Rule 3"
            title="Wave 4 never overlaps Wave 1 price territory"
            body="In a non-diagonal impulse, Wave 4 cannot trade into the range covered by Wave 1. Diagonals are the only valid exception."
            color="down"
          />
        </div>
      </Section>

      {/* ── 2. Basic Structure ──────────────────────────────────────────── */}
      <Section
        title="2 · Basic Structure"
        subtitle="Every trend subdivides 5-3: five motive waves followed by three corrective waves."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-md border border-border bg-bg-subtle p-4">
            <h3 className="mb-3 text-sm font-semibold text-fg-base">
              Motive — 5-wave impulse
            </h3>
            <div className="rounded-sm bg-bg-base/60 px-2 py-2">
              <WaveSvg pts={DIAGRAMS.impulse.pts} vb={DIAGRAMS.impulse.vb} />
            </div>
            <ul className="mt-3 space-y-1 text-xs text-fg-subtle">
              <li>
                <Dot color="up" /> Waves 1, 3, 5 move <em>with</em> the trend
              </li>
              <li>
                <Dot color="down" /> Waves 2, 4 are corrective retracements
              </li>
              <li>
                <Dot color="accent" /> Wave 3 is typically longest &amp; strongest
              </li>
            </ul>
          </div>
          <div className="rounded-md border border-border bg-bg-subtle p-4">
            <h3 className="mb-3 text-sm font-semibold text-fg-base">
              Corrective — 3-wave ABC
            </h3>
            <div className="rounded-sm bg-bg-base/60 px-2 py-2">
              <WaveSvg pts={DIAGRAMS.zigzag.pts} vb={DIAGRAMS.zigzag.vb} />
            </div>
            <ul className="mt-3 space-y-1 text-xs text-fg-subtle">
              <li>
                <Dot color="down" /> A &amp; C move <em>against</em> the higher-degree trend
              </li>
              <li>
                <Dot color="up" /> B is a counter-correction (partial recovery)
              </li>
              <li>
                <Dot color="accent" /> Corrective patterns have far more variety than
                impulses
              </li>
            </ul>
          </div>
        </div>
      </Section>

      {/* ── 3. Wave Personalities ───────────────────────────────────────── */}
      <Section
        title="3 · Wave Personalities"
        subtitle="Each wave has a characteristic sentiment, volume, and news backdrop. Recognizing these helps confirm counts in real time."
      >
        <div className="space-y-2">
          {WAVE_PERSONALITIES.map((w) => (
            <PersonalityRow key={w.wave} {...w} />
          ))}
        </div>
      </Section>

      {/* ── 4. Motive Structures ────────────────────────────────────────── */}
      <Section
        title="4 · Motive Structures"
        subtitle="Structures that move in the direction of the one-larger-degree trend."
      >
        <div className="space-y-3">
          <StructureCard
            name="Impulse"
            sub="5-3-5-3-5"
            badge="most common"
            badgeColor="accent"
            diagram={DIAGRAMS.impulse}
            diagramNote="W3 is always the longest motive wave"
            points={[
              "The classic trending structure. Five waves: 1-2-3-4-5.",
              "Internal subdivisions: Waves 1, 3, 5 → impulse or diagonal; Waves 2, 4 → any corrective.",
              "All three hard rules apply.",
              "Wave 5 typically shows momentum divergence (new price high, lower indicator reading).",
            ]}
          />
          <StructureCard
            name="Ending Diagonal"
            sub="3-3-3-3-3 or 5-3-5-3-5"
            badge="wave 5 or C"
            badgeColor="down"
            diagram={DIAGRAMS.ending_diagonal}
            diagramNote="W4 overlaps W1 — the defining feature; waves contract into a wedge"
            points={[
              "Appears as Wave 5 of an impulse or Wave C of a zigzag — signals exhaustion.",
              "Wave 4 MUST overlap Wave 1 (the only valid exception to Rule 3).",
              "Waves contract: W3 < W1, W5 < W3, W4 < W2. Creates a converging wedge shape.",
              "Corrective retracements (W2, W4) are deep: 61.8–78.6% of prior motive wave.",
              "After completion expect a sharp, fast reversal back to the diagonal's origin.",
            ]}
          />
          <StructureCard
            name="Leading Diagonal"
            sub="3-3-3-3-3 or 5-3-5-3-5"
            badge="wave 1 or A"
            badgeColor="up"
            diagram={DIAGRAMS.leading_diagonal}
            diagramNote="Same wedge shape as ending diagonal; appears at the start of a new trend"
            points={[
              "Appears as Wave 1 of an impulse or Wave A of a zigzag — marks the start of a new trend.",
              "Same overlapping, contracting wedge shape as the ending diagonal.",
              "Wave 4 overlap with Wave 1 is typical but not always mandatory.",
              "Much less common than ending diagonals; harder to identify in real time.",
              "After a leading diagonal expect a deep Wave 2 retrace (often 78.6–100% of Wave 1).",
            ]}
          />
        </div>
      </Section>

      {/* ── 5. Corrective Structures ────────────────────────────────────── */}
      <Section
        title="5 · Corrective Structures"
        subtitle="Structures that move against the one-larger-degree trend. Never label a correction until the move is complete."
      >
        <div className="space-y-3">
          <StructureCard
            name="Zigzag"
            sub="A(5)-B(3)-C(5)"
            badge="sharp correction"
            badgeColor="accent"
            diagram={DIAGRAMS.zigzag}
            diagramNote="B retraces 38–85% of A; C = 61.8–123.6% of A; sharpest corrective pattern"
            points={[
              "The sharpest corrective pattern — makes the most ground against the prior trend.",
              "Wave A and C subdivide into 5 waves (impulse or diagonal).",
              "Wave B retraces 38.2–85.4% of Wave A (most common: 50–61.8%).",
              "Wave C typically equals 61.8%, 100%, or 123.6% of Wave A.",
              "Key rule: B cannot retrace past the origin of A.",
            ]}
          />
          <StructureCard
            name="Regular Flat"
            sub="A(3)-B(3)-C(5)"
            badge="sideways"
            badgeColor="accent"
            diagram={DIAGRAMS.regular_flat}
            diagramNote="B retraces ≈90–105% of A — near full retrace is the hallmark"
            points={[
              "Waves A and B are both 3-wave structures (not impulses).",
              "Wave B retraces ≈90–105% of Wave A — near full retrace is the defining feature.",
              "Wave C ends slightly beyond Wave A's endpoint.",
              "C typically = 61.8–123.6% of waves A+B combined.",
            ]}
          />
          <StructureCard
            name="Expanded Flat"
            sub="A(3)-B(3)-C(5)"
            badge="extended B"
            badgeColor="down"
            diagram={DIAGRAMS.expanded_flat}
            diagramNote="B exceeds the origin (>100% of A), C extends well beyond A"
            points={[
              "Wave B exceeds Wave A's starting point (B > 100% of A, often 123.6%).",
              "Wave C ends substantially beyond Wave A's endpoint.",
              "C typically = 123.6–161.8% of waves A+B combined.",
              "Common and often mistaken for an impulse continuation — watch for momentum divergence on C.",
            ]}
          />
          <StructureCard
            name="Running Flat"
            sub="A(3)-B(3)-C(5)"
            badge="strong trend"
            badgeColor="up"
            diagram={DIAGRAMS.running_flat}
            diagramNote="B exceeds origin; C falls SHORT of A — the correction barely pauses the trend"
            points={[
              "Wave B exceeds Wave A's starting point (like expanded flat, B ≈ 123.6% of A).",
              "Wave C falls SHORT of Wave A's endpoint — the correction 'fails' to make ground.",
              "Signals an exceptionally strong underlying trend; the correction barely pauses it.",
              "After a running flat expect acceleration in the primary trend direction.",
            ]}
          />
          <StructureCard
            name="Contracting Triangle"
            sub="A(3)-B(3)-C(3)-D(3)-E(3)"
            badge="wave 4 or B"
            badgeColor="accent"
            diagram={DIAGRAMS.triangle}
            diagramNote="Each wave shorter than the last; converging trendlines form the wedge"
            points={[
              "Five corrective waves with converging trendlines — ascending, descending, or symmetric.",
              "Each successive wave is shorter than the previous: B < A, C < B, D < C, E < D.",
              "Appears most often in Wave 4 of an impulse or Wave B of a flat/zigzag.",
              "No wave 4/wave 1 overlap rule applies — all internal waves are corrective.",
              "After E completes, expect a 'thrust' equal to ~|Wave A| in the resuming trend direction.",
            ]}
          />
          <StructureCard
            name="Double Three (WXY)"
            sub="W-X-Y (7 swings)"
            badge="complex"
            badgeColor="down"
            points={[
              "Two corrective patterns joined by a connecting Wave X.",
              "W and Y can each be a zigzag, flat, or triangle (any combination).",
              "Wave X retraces 50–85.4% of Wave W.",
              "Wave Y = 61.8–123.6% of Wave W (cannot exceed 161.8%).",
              "Results in a broader, more time-consuming sideways correction than a simple A-B-C.",
            ]}
          />
          <StructureCard
            name="Triple Three (WXYXZ)"
            sub="W-X-Y-X-Z (11 swings)"
            badge="rare"
            badgeColor="down"
            points={[
              "Three corrective patterns joined by two connecting X waves.",
              "W, Y, Z can each be a zigzag, flat, or double three.",
              "Same Fibonacci relationships as double three but repeated once more.",
              "Usually only identifiable in hindsight. If you think you see one, simplify the count.",
            ]}
          />
        </div>
      </Section>

      {/* ── 6. Fibonacci Relationships ──────────────────────────────────── */}
      <Section
        title="6 · Fibonacci Relationships"
        subtitle="Fibonacci ratios define the most probable reversal zones and extension targets. These are guidelines — not rules. Expect clusters of ratios for high-confidence levels."
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-fg-subtle">
                <th className="pb-2 pr-4">Wave</th>
                <th className="pb-2 pr-4">Measured vs</th>
                <th className="pb-2">Key Ratios</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {FIB_ROWS.map((r) => (
                <tr key={r.wave}>
                  <td className="py-2 pr-4 font-mono font-medium text-fg-base">{r.wave}</td>
                  <td className="py-2 pr-4 text-fg-muted">{r.vs}</td>
                  <td className="py-2">
                    <div className="flex flex-wrap gap-1.5">
                      {r.ratios.map(({ pct, note }) => (
                        <span
                          key={pct}
                          title={note}
                          className={cn(
                            "rounded-sm px-1.5 py-0.5 font-mono text-[10px]",
                            note === "ideal"
                              ? "bg-accent/20 text-accent font-semibold"
                              : "bg-bg-muted text-fg-muted",
                          )}
                        >
                          {pct}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-fg-subtle">
          <span className="inline-block rounded-sm bg-accent/20 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-accent">
            highlighted
          </span>{" "}
          = most common / ideal ratio. Others are valid but less frequent.
        </p>
      </Section>

      {/* ── 7. Wave Degrees ─────────────────────────────────────────────── */}
      <Section
        title="7 · Wave Degrees"
        subtitle="Elliott identified 9 degrees, from multi-decade Grand Super Cycles down to subminuette waves. Each degree nests cleanly inside the one above it."
      >
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {DEGREES.map((d) => (
            <div
              key={d.name}
              className="rounded-md border border-border bg-bg-subtle px-3 py-2"
            >
              <div className="font-mono text-xs font-semibold text-fg-base">{d.name}</div>
              <div className="text-[10px] text-fg-subtle">{d.timeframe}</div>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-fg-subtle">
          This engine operates across degrees 0–3, using k-pivot windows of 4, 8, 16, and 32
          bars to synthesize multi-degree counts simultaneously.
        </p>
      </Section>

      {/* ── 8. Modern Market Notes ──────────────────────────────────────── */}
      <Section
        title="8 · Modern Market Notes"
        subtitle="Elliott developed his theory in the 1930s. Algorithmic trading has changed market microstructure in ways that require updated interpretation."
      >
        <div className="space-y-2">
          {MODERN_NOTES.map((n) => (
            <div
              key={n.title}
              className="rounded-md border border-border bg-bg-subtle px-3 py-2.5"
            >
              <div className="text-xs font-semibold text-fg-base">{n.title}</div>
              <div className="mt-0.5 text-xs text-fg-subtle">{n.body}</div>
            </div>
          ))}
        </div>
      </Section>

      {/* ── 9. Engine Coverage ──────────────────────────────────────────── */}
      <Section
        title="9 · What This Engine Implements"
        subtitle="Coverage map for the StockAlert Elliott Wave engine (ew3.9.0)."
      >
        <div className="grid gap-2 sm:grid-cols-2">
          {ENGINE_COVERAGE.map((item) => (
            <div
              key={item.label}
              className="flex items-start gap-2 rounded-md border border-border bg-bg-subtle px-3 py-2"
            >
              <span
                className={cn(
                  "mt-0.5 shrink-0 rounded-sm px-1.5 py-0.5 text-[9px] font-bold uppercase",
                  item.status === "yes"
                    ? "bg-up/15 text-up"
                    : item.status === "partial"
                      ? "bg-accent/20 text-accent"
                      : "bg-bg-muted text-fg-muted",
                )}
              >
                {item.status === "yes" ? "✓" : item.status === "partial" ? "~" : "—"}
              </span>
              <div>
                <div className="text-xs font-medium text-fg-base">{item.label}</div>
                {item.note ? (
                  <div className="text-[10px] text-fg-subtle">{item.note}</div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </Section>

      <footer className="border-t border-border pt-6 text-xs text-fg-subtle">
        Sources: R.N. Elliott, <em>The Wave Principle</em> (1938); Frost &amp; Prechter,{" "}
        <em>Elliott Wave Principle</em> (1978); elliottwave-forecast.com. Fibonacci
        relationships and wave personality descriptions are EWT guidelines (probabilistic),
        not rules. Always confirm counts with price action and momentum indicators.
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Wave SVG renderer
// ---------------------------------------------------------------------------

interface WavePt {
  x: number;
  y: number;
  label: string;
}

interface Channel {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

function isLocalHigh(pts: WavePt[], i: number): boolean {
  const py = i > 0 ? pts[i - 1].y : Infinity;
  const ny = i < pts.length - 1 ? pts[i + 1].y : Infinity;
  return pts[i].y <= py && pts[i].y <= ny;
}

function WaveSvg({
  pts,
  vb = "0 0 240 90",
  channels,
}: {
  pts: WavePt[];
  vb?: string;
  channels?: Channel[];
}) {
  const parts = vb.split(" ").map(Number);
  const VW = parts[2];

  return (
    <svg
      viewBox={vb}
      className="w-full"
      style={{ height: 76 }}
      aria-hidden="true"
    >
      {/* Optional trendline channels */}
      {channels?.map((ch, i) => (
        <line
          key={i}
          x1={ch.x1}
          y1={ch.y1}
          x2={ch.x2}
          y2={ch.y2}
          stroke="hsl(var(--fg-subtle))"
          strokeWidth="0.7"
          strokeDasharray="3 3"
          opacity="0.55"
        />
      ))}

      {/* Wave segments colored by direction */}
      {pts.slice(0, -1).map((p, i) => {
        const q = pts[i + 1];
        const goingUp = q.y < p.y;
        return (
          <line
            key={i}
            x1={p.x}
            y1={p.y}
            x2={q.x}
            y2={q.y}
            stroke={goingUp ? "hsl(var(--up))" : "hsl(var(--down))"}
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        );
      })}

      {/* Pivot dots + labels */}
      {pts.map((p, i) => {
        const prev = i > 0 ? pts[i - 1] : null;
        const goingUp = prev ? p.y < prev.y : false;
        const dotFill =
          i === 0
            ? "hsl(var(--fg-subtle))"
            : goingUp
              ? "hsl(var(--up))"
              : "hsl(var(--down))";

        const high = isLocalHigh(pts, i);
        const labelY = high ? p.y - 6 : p.y + 11;
        const textAnchor =
          p.x < 22 ? "start" : p.x > VW - 22 ? "end" : "middle";

        return (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r="2.8" fill={dotFill} />
            <text
              x={p.x}
              y={labelY}
              textAnchor={textAnchor}
              fontSize="8.5"
              fill="hsl(var(--fg-muted))"
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              fontWeight="700"
            >
              {p.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Diagram definitions
// ---------------------------------------------------------------------------

interface DiagramSpec {
  pts: WavePt[];
  vb?: string;
  channels?: Channel[];
}

const DIAGRAMS: Record<string, DiagramSpec> = {
  // Classic up impulse: W3 tallest, alternation W2 deep / W4 shallow
  impulse: {
    pts: [
      { x: 12, y: 82, label: "0" },
      { x: 54, y: 40, label: "1" },
      { x: 82, y: 57, label: "2" },
      { x: 150, y: 10, label: "3" },
      { x: 178, y: 33, label: "4" },
      { x: 228, y: 8, label: "5" },
    ],
    vb: "0 0 240 94",
  },

  // Ending diagonal (up wedge): W4 overlaps W1, each wave shorter, converging channels
  ending_diagonal: {
    pts: [
      { x: 12, y: 82, label: "0" },
      { x: 54, y: 37, label: "1" },
      { x: 85, y: 62, label: "2" },
      { x: 135, y: 24, label: "3" },
      { x: 163, y: 46, label: "4" }, // y=46 > y=37 → into W1 territory ✓
      { x: 205, y: 14, label: "5" },
    ],
    vb: "0 0 220 94",
    channels: [
      // Upper trendline through W1, W3, W5 (converging up)
      { x1: 54, y1: 37, x2: 205, y2: 14 },
      // Lower trendline through W0, W2, W4 (converging up, steeper)
      { x1: 12, y1: 82, x2: 163, y2: 46 },
    ],
  },

  // Leading diagonal: same wedge shape, wave 1/A position
  leading_diagonal: {
    pts: [
      { x: 12, y: 82, label: "0" },
      { x: 54, y: 37, label: "1" },
      { x: 85, y: 62, label: "2" },
      { x: 133, y: 24, label: "3" },
      { x: 160, y: 46, label: "4" },
      { x: 202, y: 14, label: "5" },
    ],
    vb: "0 0 220 94",
    channels: [
      { x1: 54, y1: 37, x2: 202, y2: 14 },
      { x1: 12, y1: 82, x2: 160, y2: 46 },
    ],
  },

  // Zigzag (down correction): A sharp, B partial recovery, C below A
  zigzag: {
    pts: [
      { x: 12, y: 12, label: "0" },
      { x: 82, y: 76, label: "A" },
      { x: 138, y: 38, label: "B" }, // B retraces ~59% of A ✓
      { x: 225, y: 84, label: "C" }, // C below A ✓
    ],
    vb: "0 0 240 94",
  },

  // Regular flat (down): B near-fully retraces A (~93%), C slightly below A
  regular_flat: {
    pts: [
      { x: 12, y: 14, label: "0" },
      { x: 82, y: 76, label: "A" }, // A down 62 units
      { x: 157, y: 18, label: "B" }, // B retraces 58/62 = 93% ✓
      { x: 228, y: 82, label: "C" }, // C just below A ✓
    ],
    vb: "0 0 240 94",
  },

  // Expanded flat (down): B exceeds origin (>100% A), C extends well past A
  expanded_flat: {
    pts: [
      { x: 12, y: 22, label: "0" },
      { x: 82, y: 73, label: "A" }, // A down 51 units
      { x: 157, y: 7, label: "B" },  // B exceeds origin: y7 < y22 ✓; B = 66 units > A ✓
      { x: 228, y: 86, label: "C" }, // C well below A ✓
    ],
    vb: "0 0 240 94",
  },

  // Running flat (down): B exceeds origin, C fails to reach A's endpoint
  running_flat: {
    pts: [
      { x: 12, y: 22, label: "0" },
      { x: 82, y: 76, label: "A" }, // A down 54 units
      { x: 157, y: 7, label: "B" }, // B exceeds origin ✓
      { x: 228, y: 59, label: "C" }, // C fails short of A (59 < 76) ✓
    ],
    vb: "0 0 240 94",
  },

  // Contracting triangle: A-B-C-D-E with converging trendlines
  triangle: {
    pts: [
      { x: 12, y: 18, label: "0" },
      { x: 55, y: 79, label: "A" },
      { x: 98, y: 28, label: "B" },
      { x: 140, y: 68, label: "C" },
      { x: 176, y: 40, label: "D" },
      { x: 210, y: 57, label: "E" },
    ],
    vb: "0 0 226 94",
    channels: [
      // Upper: highs converging downward (0, B, D)
      { x1: 12, y1: 18, x2: 176, y2: 40 },
      // Lower: lows converging upward (A, C, E)
      { x1: 55, y1: 79, x2: 210, y2: 57 },
    ],
  },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-fg-base">{title}</h2>
        <p className="text-xs text-fg-subtle">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

function RuleCard({
  number,
  title,
  body,
  color,
}: {
  number: string;
  title: string;
  body: string;
  color: "up" | "down" | "accent";
}) {
  const cls = {
    up: "border-up/40 bg-up/5",
    down: "border-down/40 bg-down/5",
    accent: "border-accent/40 bg-accent/5",
  }[color];
  const badge = {
    up: "bg-up/15 text-up",
    down: "bg-down/15 text-down",
    accent: "bg-accent/20 text-accent",
  }[color];
  return (
    <div className={cn("rounded-md border p-3", cls)}>
      <span
        className={cn(
          "mb-2 inline-block rounded-sm px-1.5 py-0.5 text-[10px] font-bold uppercase",
          badge,
        )}
      >
        {number}
      </span>
      <div className="text-xs font-semibold text-fg-base">{title}</div>
      <p className="mt-1 text-[11px] leading-relaxed text-fg-subtle">{body}</p>
    </div>
  );
}

function PersonalityRow({
  wave,
  sentiment,
  volume,
  news,
  color,
}: {
  wave: string;
  sentiment: string;
  volume: string;
  news: string;
  color: "up" | "down" | "neutral";
}) {
  const badge = {
    up: "bg-up/15 text-up",
    down: "bg-down/15 text-down",
    neutral: "bg-bg-muted text-fg-muted",
  }[color];
  return (
    <div className="flex items-start gap-3 rounded-md border border-border bg-bg-subtle px-3 py-2.5">
      <span
        className={cn(
          "shrink-0 rounded-sm px-2 py-0.5 font-mono text-xs font-bold",
          badge,
        )}
      >
        {wave}
      </span>
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="text-xs font-medium text-fg-base">{sentiment}</div>
        <div className="text-[11px] text-fg-subtle">
          Volume: {volume} · News: {news}
        </div>
      </div>
    </div>
  );
}

function StructureCard({
  name,
  sub,
  badge,
  badgeColor,
  points,
  diagram,
  diagramNote,
}: {
  name: string;
  sub: string;
  badge: string;
  badgeColor: "up" | "down" | "accent";
  points: string[];
  diagram?: DiagramSpec;
  diagramNote?: string;
}) {
  const badgeCls = {
    up: "bg-up/15 text-up",
    down: "bg-down/15 text-down",
    accent: "bg-accent/20 text-accent",
  }[badgeColor];

  return (
    <div className="rounded-md border border-border bg-bg-subtle p-3">
      {/* Header row */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-semibold text-fg-base">{name}</span>
        <span className="font-mono text-xs text-fg-muted">{sub}</span>
        <span
          className={cn(
            "ml-auto rounded-sm px-1.5 py-0.5 text-[10px] uppercase",
            badgeCls,
          )}
        >
          {badge}
        </span>
      </div>

      {/* Body: diagram left, bullets right on sm+ */}
      <div className={cn("flex flex-col gap-3", diagram && "sm:flex-row sm:gap-4")}>
        {diagram && (
          <div className="shrink-0 sm:w-[44%]">
            <div className="rounded-sm bg-bg-base/70 px-2 pt-2 pb-1">
              <WaveSvg
                pts={diagram.pts}
                vb={diagram.vb}
                channels={diagram.channels}
              />
            </div>
            {diagramNote && (
              <p className="mt-1.5 text-[10px] leading-snug text-fg-subtle">
                {diagramNote}
              </p>
            )}
          </div>
        )}

        <ul className="flex-1 space-y-1.5">
          {points.map((p, i) => (
            <li
              key={i}
              className="flex items-start gap-1.5 text-[11px] text-fg-subtle"
            >
              <span className="mt-0.5 shrink-0 text-[8px] text-fg-muted">▸</span>
              {p}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Dot({ color }: { color: "up" | "down" | "accent" }) {
  const cls = {
    up: "bg-up",
    down: "bg-down",
    accent: "bg-accent",
  }[color];
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full", cls)} />;
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

const WAVE_PERSONALITIES = [
  {
    wave: "W1",
    sentiment: "Nascent — most don't believe it yet. Prior trend still feels dominant.",
    volume: "Slightly increased, not alarming",
    news: "Still negative / cautious",
    color: "up" as const,
  },
  {
    wave: "W2",
    sentiment: "Bearish again. Many believe the prior downtrend has resumed.",
    volume: "Lower than Wave 1",
    news: "Negative, fundamentals weak",
    color: "down" as const,
  },
  {
    wave: "W3",
    sentiment:
      "Conviction grows. Crowd begins participating at mid-wave. Gaps up often signal progression.",
    volume: "Highest of the impulse",
    news: "Turning positive; earnings estimates rising",
    color: "up" as const,
  },
  {
    wave: "W4",
    sentiment:
      "Frustrating sideways churn. Shallow and time-consuming relative to the move size.",
    volume: "Well below Wave 3",
    news: "Mixed to neutral",
    color: "neutral" as const,
  },
  {
    wave: "W5",
    sentiment:
      "Euphoric near the top. Average investors finally buy. Momentum diverges from price.",
    volume: "Often less than Wave 3",
    news: "Universally positive — everyone bullish",
    color: "up" as const,
  },
  {
    wave: "WA",
    sentiment: "Drop seen as a pullback. Still viewed as a buying opportunity.",
    volume: "Increasing; volatility rises",
    news: "Still positive; market 'just correcting'",
    color: "down" as const,
  },
  {
    wave: "WB",
    sentiment:
      "Bull trap. Looks like the trend resumed but fundamentals are no longer improving.",
    volume: "Lower than Wave A",
    news: "Neutral to cautiously positive",
    color: "neutral" as const,
  },
  {
    wave: "WC",
    sentiment:
      "Bear market becomes obvious by Wave C's third leg. Capitulation and fear.",
    volume: "Picks up again",
    news: "Negative; recession / bear market consensus",
    color: "down" as const,
  },
];

const FIB_ROWS = [
  {
    wave: "Wave 2",
    vs: "Wave 1",
    ratios: [
      { pct: "50%", note: "common" },
      { pct: "61.8%", note: "ideal" },
      { pct: "76.4%", note: "common" },
      { pct: "85.4%", note: "deep but valid" },
    ],
  },
  {
    wave: "Wave 3",
    vs: "Wave 1",
    ratios: [
      { pct: "161.8%", note: "ideal" },
      { pct: "200%", note: "common" },
      { pct: "261.8%", note: "extended" },
      { pct: "323.6%", note: "rare" },
    ],
  },
  {
    wave: "Wave 4",
    vs: "Wave 3",
    ratios: [
      { pct: "14.6%", note: "shallow" },
      { pct: "23.6%", note: "common" },
      { pct: "38.2%", note: "ideal" },
      { pct: "50%", note: "max" },
    ],
  },
  {
    wave: "Wave 5",
    vs: "Wave 1 or Wave 4",
    ratios: [
      { pct: "61.8% of W1", note: "common" },
      { pct: "100% of W1", note: "ideal" },
      { pct: "123.6–161.8% of W4", note: "extension" },
    ],
  },
  {
    wave: "ZZ Wave B",
    vs: "Wave A",
    ratios: [
      { pct: "50%", note: "common" },
      { pct: "61.8%", note: "ideal" },
      { pct: "76.4%", note: "common" },
      { pct: "85.4%", note: "deep" },
    ],
  },
  {
    wave: "ZZ Wave C",
    vs: "Wave A",
    ratios: [
      { pct: "61.8%", note: "common" },
      { pct: "100%", note: "ideal" },
      { pct: "123.6%", note: "extended" },
    ],
  },
  {
    wave: "Flat B",
    vs: "Wave A",
    ratios: [
      { pct: "90–105%", note: "regular flat" },
      { pct: "123.6%", note: "ideal (expanded/running)" },
      { pct: "138.2%", note: "max (expanded)" },
    ],
  },
  {
    wave: "Flat C",
    vs: "Wave A (or AB)",
    ratios: [
      { pct: "61.8%", note: "running flat" },
      { pct: "100%", note: "regular flat" },
      { pct: "123.6%", note: "ideal" },
      { pct: "161.8%", note: "expanded flat" },
    ],
  },
  {
    wave: "Triangle legs",
    vs: "Prior leg",
    ratios: [
      { pct: "61.8%", note: "ideal" },
      { pct: "50–85%", note: "valid range" },
    ],
  },
];

const DEGREES = [
  { name: "Grand Super Cycle", timeframe: "Decades–centuries" },
  { name: "Super Cycle", timeframe: "Decades" },
  { name: "Cycle", timeframe: "Years" },
  { name: "Primary", timeframe: "Months–years" },
  { name: "Intermediate", timeframe: "Weeks–months" },
  { name: "Minor", timeframe: "Weeks" },
  { name: "Minute", timeframe: "Days" },
  { name: "Minuette", timeframe: "Hours" },
  { name: "Subminuette", timeframe: "Minutes" },
];

const MODERN_NOTES = [
  {
    title: "3-wave trends are now common",
    body: "In Elliott's era (1930s), trends always unfolded in 5 waves. Modern algorithmic trading has made 3-wave (corrective-labeled) trends far more frequent, especially in forex and futures. Don't force a 5-wave count if the market is clearly trending in 3.",
  },
  {
    title: "Momentum divergence is non-optional",
    body: "Wave 5 endpoints, flat C-wave endpoints, and diagonal completions must show momentum divergence (price makes new extreme; RSI/MACD does not). Without divergence, the wave count is unconfirmed.",
  },
  {
    title: "No look-ahead",
    body: "A valid count can only use pivots confirmed at or before the analysis timestamp. Fitting waves to future bars is curve-fitting, not Elliott Wave analysis.",
  },
  {
    title: "Uncertainty is information",
    body: "When two or more competing counts have similar probability, the market is genuinely ambiguous. High uncertainty (>50%) means reduce size or stay out — not force a label.",
  },
];

const ENGINE_COVERAGE = [
  { label: "Impulse (5-wave)", status: "yes" as const, note: "Full rules + Fibonacci scoring" },
  { label: "Truncation flag", status: "yes" as const, note: "W5 fails to exceed W3; confidence ×0.88" },
  { label: "Zigzag (A-B-C)", status: "yes" as const, note: "Updated to 85.4% B-retrace range" },
  { label: "Regular flat", status: "yes" as const, note: "B ≥ 90% rule + Fib scoring" },
  { label: "Expanded flat", status: "yes" as const, note: "B > 105% band included" },
  { label: "Running flat", status: "yes" as const, note: "Scored via short-C Fib band; rationale notes" },
  { label: "Contracting triangle", status: "yes" as const, note: "A-B-C-D-E; thrust target" },
  { label: "Ending diagonal", status: "yes" as const, note: "W4/W1 overlap required; tagged in output" },
  {
    label: "Leading diagonal",
    status: "partial" as const,
    note: "Detected same as ending; type distinction pending multi-degree context",
  },
  { label: "Double Three (WXY)", status: "no" as const, note: "Planned — complex corrective not yet scored" },
  { label: "Triple Three (WXYXZ)", status: "no" as const, note: "Planned — rarely identifiable in real time" },
  { label: "Multi-degree synthesis", status: "yes" as const, note: "k = 4, 8, 16, 32 pivot windows pooled" },
  { label: "V3-3 Scenarios", status: "yes" as const, note: "Primary + alternates with gate prices" },
  { label: "Personality bonus (V3-4)", status: "yes" as const, note: "W3/W5 in-progress extension credit" },
  { label: "Nesting score (V3-1)", status: "yes" as const, note: "Sub-wave subdivision validation" },
  { label: "Forward projection (V3-2)", status: "yes" as const, note: "Next wave target + invalidation" },
];
