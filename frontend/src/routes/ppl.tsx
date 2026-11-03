import { useState } from "react";
import { AlertTriangle, RefreshCw, Search } from "lucide-react";
import { useParams } from "react-router-dom";
import {
  usePosteriorPredictiveLevels,
  type PplDriftMode,
  type PplInterval,
  type PplLevel,
  type PplVolModel,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { FanChart } from "@/components/ppl/FanChart";
import { Button } from "@/components/ui/button";
import { fmtAgo } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Posterior Predictive Levels — the fan chart and its extracted levels.
 *
 * Operator-only while PIT calibration is outstanding (spec D2). The levels
 * shown are QUANTILES OF A SIMULATED DISTRIBUTION with no validated coverage
 * yet, and the page says so rather than letting the picture imply otherwise.
 */

const DEFAULT_SYMBOL = "SPY";
const INTERVALS: PplInterval[] = ["30m", "1h", "4h", "1d"];
const HORIZONS = [20, 40, 60, 100];

const DRIFT_MODE_LABEL: Record<PplDriftMode, string> = {
  zero: "no drift",
  unconditional: "baseline drift",
  state: "state drift",
};

/** Three separable claims, only one of which the evidence rejects. */
const DRIFT_MODE_HELP: Record<PplDriftMode, string> = {
  zero:
    "No drift at all — a pure volatility forecast. Note this is itself a claim, " +
    "and PIT rejects it for equity indices: their baseline drift is real.",
  unconditional:
    "The symbol's own average log return over the fit window. Not a timing " +
    "claim — for an equity index this is the risk premium. The mode calibration " +
    "was measured under.",
  state:
    "The shrunk PER-STATE conditional drift — a timing claim that this " +
    "platform's evidence rejects (a max-of-32-states pick at t≈3).",
};

/** Signed — for distances from spot, where direction is the point. */
function signedPct(x: number, digits = 2) {
  return `${x >= 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`;
}

/** Unsigned — for magnitudes (a standard deviation has no direction). */
function magnitudePct(x: number, digits = 2) {
  return `${(x * 100).toFixed(digits)}%`;
}

export function PplPage() {
  const { ticker } = useParams<{ ticker?: string }>();
  const initial = (ticker ?? DEFAULT_SYMBOL).toUpperCase();

  const [draft, setDraft] = useState(initial);
  const [symbol, setSymbol] = useState(initial);
  const [interval, setInterval] = useState<PplInterval>("30m");
  const [horizonBars, setHorizonBars] = useState(40);
  const [driftMode, setDriftMode] = useState<PplDriftMode>("zero");
  const [hovered, setHovered] = useState<PplLevel | null>(null);

  const q = usePosteriorPredictiveLevels({
    symbol,
    interval,
    horizonBars,
    nPaths: 2000,
    nRenderPaths: 300,
    driftMode,
  });
  const snap = q.data;

  return (
    <div className="space-y-4 p-4">
      {/* ── controls ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            Posterior Predictive Levels
          </h1>
          <p className="text-xs text-muted-foreground">
            Simulated forward-price distribution — a measurement, not a signal
          </p>
        </div>

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
              aria-label="Symbol"
            />
          </div>
          <Button type="submit" size="sm" variant="secondary">
            Load
          </Button>
        </form>

        <div className="flex items-center gap-1" role="tablist" aria-label="Interval">
          {INTERVALS.map((iv) => (
            <button
              key={iv}
              role="tab"
              aria-selected={interval === iv}
              onClick={() => setInterval(iv)}
              className={cn(
                "h-8 rounded-md px-3 font-mono text-xs transition-colors",
                interval === iv
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {iv}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1" role="tablist" aria-label="Horizon">
          {HORIZONS.map((h) => (
            <button
              key={h}
              role="tab"
              aria-selected={horizonBars === h}
              onClick={() => setHorizonBars(h)}
              className={cn(
                "h-8 rounded-md px-2.5 font-mono text-xs transition-colors",
                horizonBars === h
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {h}b
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1" role="tablist" aria-label="Drift mode">
          {(["zero", "unconditional", "state"] as PplDriftMode[]).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={driftMode === m}
              onClick={() => setDriftMode(m)}
              title={DRIFT_MODE_HELP[m]}
              className={cn(
                "h-8 rounded-md px-2.5 text-xs transition-colors",
                driftMode === m
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {DRIFT_MODE_LABEL[m]}
            </button>
          ))}
        </div>

        <Button size="sm" variant="ghost" onClick={() => void q.refetch()}>
          <RefreshCw className={cn("mr-1 h-3.5 w-3.5", q.isFetching && "animate-spin")} />
          Refresh
        </Button>

        {snap && (
          <span className="ml-auto text-xs text-muted-foreground">
            anchor {fmtAgo(snap.as_of)}
          </span>
        )}
      </div>

      {q.error && <ApiErrorAlert error={q.error} />}

      {/* ── calibration provenance ───────────────────────────────
          The model HAS been calibrated; this symbol's snapshot has not. Those
          are different claims and the banner must not collapse them — saying
          "calibrated" here would let a reader infer per-symbol coverage that
          was never measured. */}
      {snap && !snap.calibration && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <div>
            <span className="font-medium text-amber-500">
              No coverage measured for this symbol.
            </span>{" "}
            <span className="text-muted-foreground">
              The <span className="font-mono">{snap.model_version}</span> model
              passes its PIT gate pooled across six ETFs at 1d and 1h — at 1h it
              is not rejected for 5 of 6 symbols, where the model it replaced was
              rejected for 5 of 6. That is evidence about the <em>model</em>. It
              is not a measurement of {snap.symbol} {snap.interval}, and these
              four levels remain quantiles of a simulation until one is run.
            </span>
          </div>
        </div>
      )}

      {snap?.stale && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs">
          <AlertTriangle className="h-4 w-4 shrink-0 text-red-500" />
          <span className="text-red-400">
            Stale feed — the anchor bar is {snap.stale_age_bars} bars old during
            an open session. A frozen feed produces confident nonsense.
          </span>
        </div>
      )}

      {q.isPending && (
        <div className="h-[520px] animate-pulse rounded-lg border border-border bg-muted/20" />
      )}

      {snap && (
        <>
          {/* ── headline ───────────────────────────────────────── */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Spot" value={snap.spot.toFixed(2)} mono />
            <Stat
              label={`σ over ${snap.horizon_bars} bars`}
              value={magnitudePct(snap.sigma_horizon)}
              mono
            />
            <Stat label="Min move" value={magnitudePct(snap.min_move)} mono />
            <Stat
              label="Vol vs normal"
              value={
                snap.vol.long_run_sigma != null && snap.vol.long_run_sigma > 0
                  ? `${(snap.vol.sigma_next / snap.vol.long_run_sigma).toFixed(2)}×`
                  : "—"
              }
              hint="Next bar's fitted volatility against this symbol's long-run level. Above 1 = currently agitated and the fan grows slower than √H as it settles; below 1 = currently calm and it grows faster."
              mono
            />
            <Stat label="Fit bars" value={String(snap.n_fit_bars)} mono />
          </div>

          <FanChart snapshot={snap} highlighted={hovered} />

          <VolModelPanel vol={snap.vol} horizonBars={snap.horizon_bars} nu={snap.nu} />

          {/* ── levels ─────────────────────────────────────────── */}
          <div className="grid gap-3 md:grid-cols-[2fr_1fr]">
            <div className="rounded-lg border border-border">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  Extracted levels with their distribution quantiles
                </caption>
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="px-3 py-2 text-left font-medium">Level</th>
                    <th className="px-3 py-2 text-right font-medium">Price</th>
                    <th className="px-3 py-2 text-right font-medium">From spot</th>
                    <th className="px-3 py-2 text-left font-medium">Meaning</th>
                  </tr>
                </thead>
                <tbody>
                  {[...snap.levels].reverse().map((l) => (
                    <tr
                      key={`${l.side}-${l.rank}-${l.price}`}
                      onMouseEnter={() => setHovered(l)}
                      onMouseLeave={() => setHovered(null)}
                      className={cn(
                        "border-b border-border/50 last:border-0 transition-colors",
                        l.suppressed && "opacity-55",
                      )}
                    >
                      <td className="px-3 py-2">
                        <span
                          className={cn(
                            "font-mono text-xs",
                            l.side === "up" ? "text-emerald-400" : "text-red-400",
                          )}
                        >
                          {l.side} · {l.rank}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {l.price.toFixed(2)}
                      </td>
                      <td
                        className={cn(
                          "px-3 py-2 text-right font-mono",
                          l.pct_from_spot >= 0 ? "text-emerald-400" : "text-red-400",
                        )}
                      >
                        {signedPct(l.pct_from_spot)}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {l.quantile !== null ? (
                          <>
                            {Math.round(l.quantile * 100)}
                            <sup>th</sup> percentile of simulated terminal price
                          </>
                        ) : (
                          "density mode"
                        )}
                        {l.suppressed && (
                          <span className="ml-1 text-amber-500">
                            — below min move, not tradeable
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* ── conditioning state ───────────────────────────── */}
            <div className="space-y-2 rounded-lg border border-border p-3">
              <h2 className="text-xs font-medium text-muted-foreground">
                Conditioning state
              </h2>
              <div className="font-mono text-xs leading-relaxed">
                {snap.state.label.split("|").map((part) => (
                  <div key={part}>{part}</div>
                ))}
              </div>
              <dl className="space-y-1 border-t border-border pt-2 text-xs">
                <Row k="observations" v={String(snap.state.observations)} />
                <Row
                  k="drift applied"
                  v={snap.drift_mode === "zero" ? "0 (none)" : snap.drift_per_bar.toExponential(2)}
                />
                <Row
                  k="state would claim"
                  v={`${snap.state_drift_per_bar.toExponential(2)}/bar`}
                />
                <Row k="bars / session" v={String(snap.bars_per_session)} />
                <Row k="paths drawn" v={String(snap.paths.length)} />
                <Row k="model" v={snap.model_version} />
              </dl>
              {snap.drift_mode === "state" && (
                <p className="text-xs text-amber-500">
                  State drift applied — a directional claim with no validated
                  coverage. Zero-drift is the default for a reason.
                </p>
              )}
              {snap.state.pooled && (
                <p className="text-xs text-amber-500">
                  Thin state — drift pooled to the unconditional mean.
                </p>
              )}
              {!snap.staleness_checked && (
                <p className="text-xs text-muted-foreground">
                  Staleness not evaluated (market closed or calendar
                  unavailable) — not a claim that the feed is current.
                </p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  mono,
  hint,
}: {
  label: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-border p-3" title={hint}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 text-lg", mono && "font-mono")}>{value}</div>
    </div>
  );
}

/**
 * The fitted volatility dynamics, on screen.
 *
 * This exists because the model IS the product: the fan is nothing but these
 * parameters rolled forward. Showing them makes the picture auditable — in
 * particular `|r| autocorrelation` before/after is the evidence that
 * bootstrapping the residuals as iid is legitimate, and a reader can see it
 * collapse rather than take it on faith.
 */
function VolModelPanel({
  vol,
  horizonBars,
  nu,
}: {
  vol: PplVolModel;
  horizonBars: number;
  nu: number;
}) {
  if (vol.kind !== "gjr_fhs") {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
        <span className="font-medium text-amber-500">Null model (ewma_t).</span>{" "}
        Constant volatility over the horizon with iid Student-t innovations
        (ν&nbsp;=&nbsp;{nu.toFixed(1)}). Retained only so calibration can measure
        against it — its variance grows as √H without bound and its fan is
        symmetric by construction.
      </div>
    );
  }

  const pct = (x: number) => `${(x * 100).toFixed(3)}%`;
  const vr = vol.variance_ratio;

  return (
    <details className="rounded-lg border border-border">
      <summary className="cursor-pointer px-3 py-2 text-xs text-muted-foreground hover:text-foreground">
        Volatility model — GJR-GARCH(1,1) + filtered historical simulation
      </summary>

      <div className="grid gap-x-6 gap-y-1 border-t border-border p-3 text-xs md:grid-cols-2">
        <Row k="σ next bar" v={pct(vol.sigma_next)} />
        <Row
          k="σ long-run"
          v={vol.long_run_sigma != null ? pct(vol.long_run_sigma) : "—"}
        />
        <Row
          k="persistence α+γ/2+β"
          v={vol.persistence != null ? vol.persistence.toFixed(4) : "—"}
        />
        <Row
          k="shock half-life"
          v={
            vol.half_life_bars != null
              ? `${vol.half_life_bars.toFixed(0)} bars`
              : "—"
          }
        />
        <Row k="α (news impact)" v={vol.alpha != null ? vol.alpha.toFixed(4) : "—"} />
        <Row k="γ (leverage)" v={vol.gamma != null ? vol.gamma.toFixed(4) : "—"} />
        <Row k="β (persistence)" v={vol.beta != null ? vol.beta.toFixed(4) : "—"} />
        <Row
          k="down-move share of impact"
          v={
            vol.vol_response_asymmetry != null
              ? `${(vol.vol_response_asymmetry * 100).toFixed(0)}%`
              : "—"
          }
        />
        <Row
          k={`var(${horizonBars}) / ${horizonBars}·var(1)`}
          v={vr != null ? vr.toFixed(3) : "—"}
        />
        <Row k="pool size" v={vol.pool_size != null ? String(vol.pool_size) : "—"} />
        <Row
          k="pool skew"
          v={vol.pool_skew != null ? vol.pool_skew.toFixed(2) : "—"}
        />
        <Row
          k="pool excess kurtosis"
          v={
            vol.pool_excess_kurtosis != null
              ? vol.pool_excess_kurtosis.toFixed(2)
              : "—"
          }
        />
        <Row k="tail index ν (descriptive)" v={nu.toFixed(1)} />
        <Row
          k="|r| autocorr — raw"
          v={vol.raw_abs_autocorr != null ? vol.raw_abs_autocorr.toFixed(3) : "—"}
        />
        <Row
          k="|r| autocorr — filtered"
          v={
            vol.residual_abs_autocorr != null
              ? vol.residual_abs_autocorr.toFixed(3)
              : "—"
          }
        />
      </div>

      <div className="space-y-1.5 border-t border-border px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
        {vr != null && (
          <p>
            <span className="text-foreground">Variance ratio {vr.toFixed(3)}.</span>{" "}
            {vr < 0.98
              ? "Below 1 — today is louder than this symbol's long-run level, so the fan grows slower than √H as volatility reverts."
              : vr > 1.02
                ? "Above 1 — today is quieter than the long-run level, so the fan grows faster than √H as volatility reverts upward."
                : "At 1 — today sits at the long-run level, so √H scaling happens to be right here."}{" "}
            Naive √H scaling assumes 1.000 always.
          </p>
        )}
        <p>
          <span className="text-foreground">
            |r| autocorrelation {vol.raw_abs_autocorr?.toFixed(3) ?? "—"} →{" "}
            {vol.residual_abs_autocorr?.toFixed(3) ?? "—"}.
          </span>{" "}
          Volatility clustering before the filter and after it. Collapsing toward
          zero is what makes resampling the residuals as independent draws
          legitimate; if it did not collapse, the bootstrap would be unsound.
        </p>
        {vol.vol_response_asymmetry != null && (
          <p>
            <span className="text-foreground">
              {(vol.vol_response_asymmetry * 100).toFixed(0)}% of the volatility
              response comes from down moves.
            </span>{" "}
            {vol.vol_response_asymmetry > 0.55
              ? "The leverage effect — losses raise tomorrow's volatility more than equivalent gains, which is what grows the fan's left tail with horizon."
              : vol.vol_response_asymmetry < 0.45
                ? "Inverted — UP moves drive volatility harder here. Real for some assets (gold), and worth noting the model measured it rather than assuming the usual sign."
                : "Near symmetric — this symbol shows little leverage effect."}
          </p>
        )}
        <p>
          Innovations are bootstrapped from the {vol.pool_size ?? "—"} fitted
          residuals — no distribution is assumed. ν is reported to describe that
          pool's tails, not to generate anything. The textbook leverage ratio
          {vol.leverage_ratio != null && ` (${vol.leverage_ratio.toFixed(1)}×)`}{" "}
          is omitted above because (α+γ)/α blows up as α→0, which is the normal
          case for index ETFs.
        </p>
      </div>
    </details>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="font-mono">{v}</dd>
    </div>
  );
}
