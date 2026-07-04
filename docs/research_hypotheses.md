# Hypothesis Registry — pre-registration for strategy research

**Rule (docs/standards/trading_subsystem.md):** register the hypothesis
family HERE, in a committed edit, BEFORE running the sweep it describes.
A family = signal family + parameter grid + universe + windows + primary
metric. The MCPT p-values of every family member are corrected together
(Benjamini-Hochberg, `app/services/sim/significance.py`); promotion to
paper trading requires walk-forward MCPT **q ≤ 0.05** within the family.

Why: 36+ experiments and dozens of grid sweeps have already been run on
this platform. The best survivor of that many searches is *expected* to
look excellent by chance. Pre-registration makes the denominator of the
multiple-hypothesis correction honest; post-hoc registration is
worthless. Registering costs one table row — there is no excuse to skip
it.

Tools: `scripts/mcpt_insample.py` (Tier-1 screen, ~1000 permutations),
`scripts/mcpt_walkforward.py` (Tier-2 full-engine, ~200 permutations),
`scripts/mcpt_ranker_labels.py` (label-permutation null for learned
models). Results land in `data/mcpt/` (gitignored — copy the numbers
into `strategy_rnd_findings.md`; data/ does not survive worktree
removal).

| ID | Registered | Family (signal × grid × universe × window) | Primary metric | Status | Verdict |
|----|------------|--------------------------------------------|----------------|--------|---------|
| H-1 | 2026-07-02 | `breakout_vol` daily (lookback {10,15,20,30,40,60} × vol_mult {1.0,1.25,1.5,2.0}), 1000-name clean universe, DEV 2022-23 screen + full-engine 2006-21 / 2024-25 | pooled PF (T1) / portfolio Sharpe (T2) | **CLOSED — luck at every tier** (EXP-37) | T1 p = 0.276; full-engine 2006-21 **p = 0.320** (96 perms), 2024-25 holdout **p = 0.254** (200 perms). Chapter closed; do not revisit without a new information source. |
| H-2 | 2026-07-02 | logistic ranker (13 as-of features), position-day top-50 dataset, split 2020-01-01 | holdout AUC vs label-permutation null | tested (EXP-38) | NOT significant: AUC 0.5552 vs null 0.4991±0.0344, **p = 0.058** — tilt at the luck boundary |

**EXP-39 signal battery** (registered 2026-07-02, BEFORE implementation
or any data contact; BH correction is across H-3..H-8 jointly). Screen:
Tier-1 in-sample MCPT, 1000-name clean universe, window **2006-01-01 →
2018-12-31**, primary metric pooled PF, 1000 permutations. 2019-2026
stays untouched for Tier-2 of survivors. All signals long-only,
close-to-close accounting.

| ID | Family | Grid | Screen verdict (raw p; BH q at battery close) |
|----|--------|------|------|
| H-3 | `meanrev_rsi` — RSI(n) oversold entry, strength exit | n {2,3,4} × entry {10,15,20,25,30} × exit {50,70} (30) | **PASS p = 0.0040** (PF 1.1002 vs null 1.0282±0.0216; best n=4/entry 10/exit 50). Random-exit locator: edge is in the ADAPTIVE EXIT (real entries + random exits → PF 1.0236, 0/500 ≥ real) — Tier-2 must implement the RSI exit faithfully. Noise test **ROBUST**: 1000 jittered histories, 100% profitable, mean 1.0979±0.0070, p5 = 1.0868. **BH q = 0.0240 → SURVIVOR, advances to Tier-2 (EXP-40).** |
| H-4 | `meanrev_zscore` — z of close vs SMA(n) entry, mean exit | n {10,20} × z {1.5,2.0,2.5} (6) | **DEAD** p = 0.1339, q = 0.2677 |
| H-5 | `xsec_momentum` — cross-sectional top-bucket by trailing return, 21-bar rebalance | lookback {60,120,12-1} × bucket {decile,quintile} (6) | **DEAD** p = 0.0390, **q = 0.1169** — raw-significant, fails FDR (the BH showcase; full-engine cousin failed independently in EXP-37) |
| H-6 | `vol_compression` — ATR%ile squeeze arm + range break entry, range-low exit | atr-pctile {0.10,0.20} × breakout lookback {5,10} (4) | **DEAD** p = 0.3437, q = 0.4843 |
| H-7 | `gap` — overnight gap trigger, fixed hold | direction {follow,fade} × gap {1,2,3}% × hold {1,3,5} (18) | **DEAD** p = 0.5504, q = 0.5504 (real below null mean) |
| H-8 | `high_52wk` — pullback low within prox of 52-wk high; exit at 2×prox distance | prox {2,5,10}% × pullback {3,5} (6) | **DEAD** p = 0.4036, q = 0.4843 |

Battery adjudicated 2026-07-03 (`mcpt_report.py --battery
'data/mcpt/exp39_t1_*.json'`): **1/6 survive at q ≤ 0.05.** Full
write-up: `strategy_rnd_findings.md` EXP-39.

**EXP-40 Tier-2 registrations** (2026-07-03, registered BEFORE the study
runs; BH across H-9/H-10 jointly). Full-engine walk-forward MCPT of the
EXP-39 survivor on the untouched window: `rsi_reversion` v0.2
(rsi_kind=wilder — decision-bar parity with the screen proven by
`tests/test_rsi_wilder_parity.py`), RSI(4) < 10 enter / > 50 exit
(faithful adaptive exit, NO stop, NO time-cap), 1000-name universe, 20
slots × 5% of cash, next-open fills, zero commission + 5 bps/side
slippage, window **2019-01-01 → 2026-06-30**, ~224-perm null
(cloud study), primary metric Sharpe, all four metrics recorded.

| ID | Config | Verdict |
|----|--------|---------|
| H-9 | `configs/rsi_meanrev_t2_bare.yaml` — the faithful validated rule | **FAILED p = 0.884, q = 0.99** (224 perms). Real: +17.8%/Sharpe 0.28/PF 1.19/DD −72%. Shuffled tapes beat it 198/224 — the null has the drift without the crashes. |
| H-10 | `configs/rsi_meanrev_t2_brake.yaml` — same + dd_brake 0.15 (crash-clustering risk governor) | **FAILED p = 0.995, q = 0.99** (192 perms). Brake caps DD (−22%) by refusing panic entries — amputates the edge (−4.6% return). Structurally incompatible with reversion. |

EXP-40 adjudicated 2026-07-03: the Tier-1 signal stands; the trade
design fails. ONE mechanism-motivated risk-expression redesign may be
registered (Wave-2); if it fails, the daily-reversion chapter closes.
Full write-up: `strategy_rnd_findings.md` EXP-40.

**EXP-41 Wave-2 battery** (registered 2026-07-03 BEFORE implementation;
BH jointly across H-11..H-14 for the Tier-1 screens; H-16 is a Tier-2
pair BH'd separately as the pre-declared single redesign). Screens:
1000-name clean universe, window 2006-01-01 → 2018-12-31, 1000
permutations, pooled PF; 2019-2026 untouched.

| ID | Family (mechanism) | Grid | Verdict |
|----|--------------------|------|---------|
| H-11 | `overnight_condition` — hold close→open only, conditioned on the prior day (risk transfer at illiquid hours; gap-return stream) | condition {down_day, up_day, down_1pct, up_1pct} (4) | **DEAD** p = 0.240, q = 0.320 |
| H-12 | `xsec_reversal` — long the cross-sectional BOTTOM bucket by trailing return, hold = lookback (liquidity provision, xsec cousin of the H-3 survivor) | lookback {5,10,21} × bucket {decile, quintile} (6) | **DEAD** p = 0.417, q = 0.417 — relative losers don't bounce |
| H-13 | `lag1_reversal` — long after a down day, 1-bar hold (simplest reversion formulation; from the user's notebook) | prior-day return < {0, −1%, −2%} (3) | **DEAD** p = 0.072, q = 0.144 — shallow-reversion whisper, fails FDR |
| H-14 | `seasonality_tom` — long the turn-of-month window (pension/401k flow) | days before month-end {3,5} × days after {2,3} (4) | **SURVIVOR p = 0.0110, q = 0.0440** (best 5/2; 10/1000 shuffles matched) → Tier-2 |
| H-15 | `fomc_drift` — long the k trading days ending at the FOMC announcement close (Lucca-Moench pre-announcement drift; scheduled meetings only) | **grid LOCKED 2026-07-03** upon calendar backfill (Fed public records → `scripts/data/fomc_scheduled_meetings.csv`): k ∈ {1, 2, 3} (3 configs). Adjudication: recompute BH across the full Wave-2 screen family H-11..H-15 (five members) as originally intended. | — |
| H-16 | **the one reversion redesign (Tier-2)** — H-9's exact rule with a hard exposure cap: crash-loading bounded by slot count, panic ENTRIES still taken (what the brake wrongly refused) | max_concurrent {4, 6} × 5% slots (20%/30% max deployment); same costs/window/nulls as EXP-40 | **BOTH FAIL**: cap4 p = 0.752 (Sharpe 0.17/DD −10.6%), cap6 p = 0.652 (Sharpe 0.35/DD −14.8%). DD engineering worked; what remains is luck-indistinguishable. **Daily-reversion chapter CLOSED** per the pre-declared rule. |

**Wave-2 candidate queue (NOT yet registered — mechanisms noted so the
future registration is honest, grids to be locked at registration time):**
overnight-vs-intraday session split (risk transfer at illiquid hours; both
prices already in daily bars) · cross-sectional short-term reversal (the
xsec cousin of H-3 — convergent-evidence test) · calendar/flow seasonality:
turn-of-month + pre-FOMC drift (FOMC dates already in `economic_data`;
permutation null is exact for calendar claims) · meanrev_rsi conditioning
(vol regime / sector / hourly / overnight-entry — survivor boundary-mapping,
not a new family) · PEAD via EDGAR events (third wave; needs an
event-study harness).

Notes:
- H-1 and H-2 are **retroactive registrations** of the already-selected
  EXP-33/EXP-36 candidates — the MCPT re-adjudication of pre-MCPT work.
  Their p-values must be read knowing the candidates were the survivors
  of many prior sweeps; a pass here is necessary, not sufficient. All
  NEW families get registered before first contact with the data.
- Closed tracks (do not re-register without new information sources):
  bar-pattern day-trading (DT-0..3), EW-as-momentum-gate (EXP-13..16),
  ranker as hard gate (EXP-31/35/36).

**EXP-42 Wave-3 battery** (registered 2026-07-03, grids LOCKED before
implementation; queued behind Wave-2 adjudication so BH families stay
clean; BH jointly across H-17..H-22). Screens: 1000-name clean universe,
2006-01-01 → 2018-12-31, pooled PF, 1000 permutations; 2019-2026
untouched. New indicators earning their place: OBV/MFI/relative-volume
+ a universe-breadth series builder (volume and participation are new
information columns; more price-transform indicators are not).

| ID | Family (mechanism) | Grid |
|----|--------------------|------|
| H-17 | `volume_capitulation` — down day on climactic volume = forced-seller exhaustion | down > {1%,2%} × vol ≥ {3,5}× avg20 × hold {1,3} (8) |
| H-18 | `breadth_timing` — participation regime times broad exposure (universe-wide signal) | rule {level>0.5, level>0.6, thrust <0.4→>0.6 in 10d} × MA {100,200} (6) |
| H-19 | `market_relative_reversion` — RSI on the stock-minus-SPY spread (idiosyncratic panic, market move removed) | n {2,4} × entry {10,15}, exit 50 (4) |
| H-20 | `xsec_lowvol_max` — monthly buckets on realized vol / prior-month max daily return (low-vol + lottery-aversion anomalies) | metric {vol63, max21} × bottom bucket {decile, quintile} (4) |
| H-21 | `leadlag_spy` — big index day → laggard constituents catch up | \|SPY\| > {1%,2%} × hold {1,3}, long the bottom-quintile laggards in the index direction (4) |
| H-22 | `survivor_conditioning` — the H-3 winner (RSI(4)<10 / >50, LOCKED — no re-tuning) gated by market regime; maps WHERE the real signal pays | gate {vol20<median252, vol20>median252, breadth>0.5, breadth<0.5} (4) |

Notes: H-19 uses SPY as the relative benchmark (no full GICS membership
map exists in the platform yet; a proper sector-relative variant needs
that mapping first and would be registered separately). H-22 is
diagnostic as much as alpha-seeking: if the panic-bounce signal pays
only in calm regimes, that is itself the crash-risk answer EXP-40
demanded.

**EXP-43 TOM Tier-2 registrations** (2026-07-03, BEFORE implementation;
BH across H-23/H-24). Full-engine walk-forward MCPT of the EXP-41
survivor on untouched **2019-01-01 → 2026-06-30**: new `calendar_tom`
engine strategy — enter when remaining calendar days in month ≤ 8 (fill
at next open ≈ 5 trading days before month-end), exit at the open after
the 2nd trading day of the new month (the tradeable next-open rendering
of the validated close-to-close window; calendar is ex-ante knowledge,
no look-ahead). Zero commission + 5 bps/side slippage, ~160-perm nulls,
primary metric Sharpe.

| ID | Config | Verdict |
|----|--------|---------|
| H-23 | `configs/tom_t2_spy.yaml` — SPY only, 95% of cash (how TOM is traded in practice: one liquid instrument, minimal friction, unbounded capacity) | **FAIL** p = 0.329 (Sharpe 0.53 vs null 0.39±0.28) |
| H-24 | `configs/tom_t2_sectors.yaml` — equal-weight across the 11 SPDR sector ETFs (9% slots; closest liquid rendering of the pooled-universe claim without 800-name turnover) | **FAIL** p = 0.149, q = 0.298 (Sharpe 0.58, +32.9%, DD −10.8% — directionally positive, unpromotable at n≈90 events). TOM PARKED. |

**EXP-44 FOMC Tier-2 registrations** (2026-07-03, BEFORE implementation;
BH across H-25/H-26). New `calendar_fomc` engine strategy: announcement
dates passed via config (ex-ante public); entry decision when the count
of weekdays strictly between the bar and the announcement equals
`pre_weekdays` (fill next open ≈ that many trading days before the
announcement; holiday slippage of one day accepted as rendering noise);
exit decision at the close before the announcement day (fill at the
announcement day's open — the position holds the pre-announcement
session(s), exiting before the 2pm decision itself). SPY only, 95% of
cash, zero commission + 5 bps/side, untouched **2019-01-01 →
2026-06-30** (60 scheduled announcements), 160-perm nulls, Sharpe
primary.

| ID | Config | Verdict |
|----|--------|---------|
| H-25 | `configs/fomc_t2_spy_k1.yaml` — pre_weekdays 1 (the validated k=1 window: hold the single day before the announcement) | FAIL p = 0.248 (Sharpe 0.19 — the next-open fill shifts the tight window off the drift) |
| H-26 | `configs/fomc_t2_spy_k2.yaml` — pre_weekdays 2 (wider window hedging the next-open timing shift) | **PASS p = 0.0062, q = 0.0124 — 0/160 shuffled tapes matched.** +27.5%/Sharpe 0.79/PF 3.17/DD −3.5% OOS. **FIRST FULL-GAUNTLET SURVIVOR → eligible for paper trading.** |

**EXP-45 registration** (2026-07-03, BEFORE implementation; single-family
BH). H-27 `spike_analog` — nonparametric analog matching: on a spike day
(|1-day return| > s × trailing-20d σ, observable at the close), find the
k nearest historical spike-windows (normalized prior-w-day return
vectors, pooled cross-symbol, STRICTLY-PRIOR history only — expanding,
no look-ahead) and take the analogs' mean next-day return as the vote;
long next bar iff the vote is positive (long-only platform). This is the
nonparametric superset of named bar patterns — a pass finds what named
patterns missed; a fail closes "pattern knowledge" as a class. Screen:
**40-name options/GEX universe** (liquid megacaps+ETFs; k-NN cost is
quadratic in trigger count, so the registered universe is the deliberate
compute bound), 2006-01-01 → 2018-12-31, pooled PF, 1000 permutations;
2019-2026 untouched.

| ID | Family | Grid |
|----|--------|------|
| H-27 | `spike_analog` | spike threshold s {2.5, 3.5} × window w {10, 20} × neighbors k {25, 100} (8) | **DEAD p = 0.4286** |

**EXP-45 addendum** (2026-07-03, registered while the H-27 screen was
still RUNNING — locked before any 1-day verdict existed). H-28
`spike_analog_multiday` — identical analog machinery, vote target and
hold extended to h-day forward returns (the user's 1-3-day scalp
horizon). Runs after H-27 adjudicates regardless of its outcome (pass →
horizon map; fail → class exhaustion before accepting the verdict).
Same 40-name universe/window/permutation count; BH within the family.

| ID | Family | Grid |
|----|--------|------|
| H-28 | `spike_analog_multiday` | s {2.5, 3.5} × w {10, 20} × k {25, 100} × horizon h {2, 3} (16) | **DEAD p = 0.4286** — pattern-matching CLOSED as a class at all tested horizons |

**EXP-46 Wave-H battery** (registered 2026-07-04 BEFORE implementation;
BH across H-29..H-31). The first HOURLY screens: session bars
(`ohlcv_hourly`, 09:30-16:00 ET, 2006-2018; 2019-2026 untouched), pooled
PF, 1000 permutations, with a SESSION-AWARE permutation kernel (bodies
shuffle within hour-of-day pools; overnight gaps shuffle only among
overnight gaps) so the null preserves intraday seasonality marginals
and destroys only serial dependence — required for honest intraday
claims. Mechanism class: flow-and-clock (the only class that has ever
passed here).

| ID | Family (mechanism) | Universe | Grid |
|----|--------------------|----------|------|
| H-29 | `intraday_momentum` — first-hour(s) return sign predicts the last hour (MOC/rebalancing/gamma flows; Gao-Han-Li-Zhou 2018) | SPY QQQ IWM DIA + 9 sector SPDRs w/ full 2006-2018 coverage (13; XLRE/XLC excluded — 2015/2018 inceptions violate the session-aware kernel's alignment requirement; amended pre-run) | predictor {first 1, first 2 bars} × min |move| {0, 0.25%} (4) | **DEAD** p = 0.0759, q = 0.1139 |
| H-30 | `fomc_hourly` — the PROMOTED drift's exit sharpened: hold announcement-day from open to just before the 2pm decision (Lucca-Moench accrual continues all morning) | SPY | exit bar {12:30, 13:30} + prior-close→13:30 variant (3). | **SURVIVOR p = 0.0010, q = 0.0030** (PF 2.7826, 0/1000; best prior_close→13:30) → Tier-2 (EXP-47) |
| H-31 | `tom_last_hour` — the parked TOM effect concentrated into the close (contribution flows execute near the close) | same 13-name aligned universe | window locked at H-14 best (5/2) × {last 1, last 2 bars} (2) | **DEAD** p = 0.6474 |

**EXP-47 Tier-2 registration** (2026-07-04, BEFORE implementation;
single hypothesis — the screen already selected the rendering). H-32:
`calendar_fomc_hourly` on SPY — entry decision at the prior day's
second-to-last hourly bar (fill at the final bar's open ≈15:30), exit
decision at the 12:30 bar's close (fill 13:30 — out before the 2pm
decision). 95% of cash, zero commission + 5bps/side, hourly interval,
untouched **2019-01-01 → 2026-06-12** (hourly lake end), 160-perm
SESSION-AWARE null, Sharpe primary. A pass registers a SECOND paper run
(`fomc_drift_hourly_spy`) beside the daily one — both live from the
2026-07-29 meeting. **VERDICT: corrected screen PF 1.6859 p=0.0020;
Tier-2 p = 0.0186 (2/160) — PASS. Enrolled 2026-07-04.** (Initial
run VOID — ET/UTC bug, see EXP-46 CORRECTION.)

**EXP-48 Wave-H2 battery** (registered 2026-07-04 BEFORE implementation;
BH across H-33..H-36). Hourly-TRIGGERED 1-3 day swing entries on
individual STOCKS — the hourly clock conditions on how the day unfolds,
not just how it closes. Universe chosen by COVERAGE (≥99.9% of SPY's
hourly bar count 2006-2018) then liquidity, 40 stocks: AAPL GOOG AMZN
BAC MSFT C XOM JPM GE INTC GS CSCO WFC NFLX PFE T CVX BIDU IBM QCOM JNJ
WMT ORCL PG NVDA SLB FCX GILD COP MRK HD CMCSA CAT F BA MU DIS KO AIG
MCD. Screen: ohlcv_hourly 2006-2018 (2019-2026 untouched), pooled PF,
1000 perms, SESSION-AWARE null, inner-join alignment (shrinkage
reported). Holds expressed as h sessions ≈ 7h hourly bars.

| ID | Family (mechanism) | Grid |
|----|--------------------|------|
| H-33 | `hourly_capitulation_swing` — intraday panic hour (return < −s × trailing-100-bar σ) → enter DURING the flush, hold h days (the real deep-panic signal, entered at the hourly trigger instead of the daily close) | s {3, 4} × h {1, 3} (4) | **DEAD** p = 0.997 |
| H-34 | `close_strength_swing` — final-2-bar strength ≥ thr → hold h days (institutional parent orders split across days; the strong CLOSE is the footprint) | thr {0.5%, 1%} × h {1, 3} (4) | **DEAD** p = 1.000 (strong closes reverse) |
| H-35 | `gap_hold_swing` — overnight gap ≥ g that HOLDS through the first two hours → enter at 2nd bar close, hold h days (the intraday confirmation daily bars can't see) | g {1%, 2%} × h {1, 3} (4) | **SURVIVOR p = 0.0010, q = 0.0040**, noise-ROBUST → Tier-2 |
| H-36 | `first_hour_break_swing` — first bar closes above the prior day's high → hold h days (ORB as a swing entry, not the cost-dead day-trade) | h {1, 3} (2) | **SURVIVOR p = 0.0090, q = 0.0180**, noise-ROBUST → Tier-2 |

**EXP-49 Tier-2 registrations** (2026-07-04, BEFORE implementation; BH
across H-37/H-38). New `hourly_swing` engine strategy (1h interval, ET
session clock): pluggable trigger locked to each screen winner; entry
decision at the trigger bar's close (fill next bar open); fixed-timer
exit ~1 session later (decision 7 bars after entry, fill next open —
the tradeable rendering of the screen's 7-bar hold). Portfolio: the
registered 40-stock universe, max 8 concurrent positions × 12% of cash,
zero commission + 5 bps/side, untouched **2019-01-01 → 2026-06-12**,
160-perm SESSION-AWARE nulls, Sharpe primary.

| ID | Config | Trigger (locked from screen) | Verdict |
|----|--------|------------------------------|---------|
| H-37 | `configs/swing_t2_gap_hold.yaml` | gap ≥ 1% vs prior close, still ≥ first open at the 2nd bar's close | **FAIL p = 0.304** (Sharpe 0.09, PF 1.01 net — see EXP-50: the edge decayed post-2019, costs were NOT the killer) |
| H-38 | `configs/swing_t2_fhb.yaml` | first bar's close > prior session's high | **FAIL p = 0.776** (Sharpe −0.36) |

**EXP-50 registration** (2026-07-04, registered while EXP-49 finals were
completing; design conditioned only on the ALREADY-KNOWN screen result
and cost arithmetic, not on unseen holdout data). The gap-hold edge is
statistically real (H-35: q = 0.004) but its h=1/g=1% portfolio
rendering nets ≈ PF 1.01 — costs consume the edge. H-39 searches for a
COST-EFFICIENT rendering with clean walls:

- **H-39-dev**: engine grid on the DEV window 2006-2018 (40-stock
  aligned hourly universe, costs included): gap g {1%, 1.5%, 2%, 3%} ×
  hold h {1, 3, 5} sessions (12 configs). Selection metric: net Sharpe.
  Pure selection — no significance claims from this stage; all 12
  results documented. Mechanism: fewer/fatter trades (higher g) and
  cost amortization (longer h).
- **H-39-holdout**: the SINGLE dev winner → full Tier-2 MCPT
  (2019-01-01 → 2026-06-12, 160-perm session-aware null, costs).
  One hypothesis; p ≤ 0.05 required. If it fails, the gap-hold chapter
  closes as "real signal, no retail-cost expression" with the entire
  g×h response surface on record. **VERDICT: dev winner g=1.5%/h=1
  (dev Sharpe 0.88 net) → holdout p = 0.348, FAIL. Chapter CLOSED —
  the edge decayed post-2019; every rendering measured.**

**EXP-51 registration** (2026-07-04, BEFORE implementation; BH across
H-40/H-41). The user's discretionary swing playbook, encoded: money-flow
continuation via (a) long bases resolving upward, (b) leaders resuming
after pullbacks. Related-but-distinct from dead cousins
(vol_compression = short ATR squeeze; high_52wk = proximity pullback;
breakout_vol = any 20d high): the differentiators are BASE LENGTH /
TIGHTNESS and LEADER-CONTEXT + RESUMPTION TRIGGER. Screens: 1000-name
daily universe, 2006-2018, pooled PF, 1000 perms; 2019-2026 untouched.
Laddered/scale-out exits (the user's risk expression — never tested on
this platform) are reserved for the Tier-2 stage of any survivor.

| ID | Family | Grid |
|----|--------|------|
| H-40 | `consolidation_breakout` — rolling base_len-day range ≤ tight × price, then close breaks the base high → fixed hold | base_len {30, 60} × tightness {8%, 12%} × hold {5, 10} (8) |
| H-41 | `leader_pullback` — ret60 ≥ lead, pulled back ≥5% off the 20d high, entry when close reclaims the prior day's high (resumption) → fixed hold | lead {30%, 50%} × hold {5, 10} (4) |

**EXP-51 addendum** (2026-07-04, registered while H-40/H-41 screens were
RUNNING — no verdicts existed; counts recorded at registration). H-42
`early_run` — catch the run BEFORE it qualifies as a +30% leader: fresh
20-day high with YOUNG momentum (ret20, not ret60) and a volume-regime
shift as the money-flow confirmation (10d avg volume vs 60d — the
institutional-accumulation footprint). Same universe/window/nulls; BH
recomputed across the full EXP-51 family H-40..H-42.

| ID | Family | Grid |
|----|--------|------|
| H-42 | `early_run` — close breaks the 20d high AND ret20 ≥ r AND vol10/vol60 ≥ v → fixed hold | r {8%, 15%} × v {1.0 (no volume gate), 1.5} × hold {5, 10} (8) |

**EXP-51 verdicts (2026-07-04, FINAL — BH across H-40..H-42):** 0/3
survive. H-40 consolidation_breakout p=0.2637 q=0.6503 (best real PF
1.107, null 1.084±0.043) — **DEAD**. H-41 leader_pullback p=0.4336
q=0.6503 (real PF 0.988 — below 1 gross) — **DEAD**. H-42 early_run
p=0.9421 q=0.9421 (real PF 0.979 UNDERPERFORMS the null mean 1.022 —
fresh-high+volume-surge entries did *worse* than random timing) —
**DEAD**. The discretionary playbook chapter closes consistent with
the campaign law: momentum/breakout structure is arbitrated away at
daily resolution regardless of the base/leader/volume framing.

**EXP-52 registration** (2026-07-04, BEFORE implementation). H-43
`asymmetric_exit_overlay` — the last untested pillar of the
discretionary-trader worldview: does "cut losses fast, let winners run"
constitute an edge BY ITSELF? Entries are deterministic and
information-free (enter when flat on every cycle-th bar per symbol);
the exit is a trailing stop (close < running-max × (1 − trail)) with no
target — pure asymmetry. The permutation null preserves fat tails and
drift exactly, so any excess PF over the null can come ONLY from exit
asymmetry harvesting real serial structure (trend persistence). A pass
= exit convexity is promotable machinery in its own right; a fail =
the creed is a payoff-shape illusion on structureless entries — either
verdict rewrites how Tier-2 expressions get built. Universe/window/
nulls as standard (1000-name daily, 2006-2018, 1000 perms).

| ID | Family | Grid |
|----|--------|------|
| H-43 | `asymmetric_exit_overlay` | entry cycle {10, 21 bars} × trail {5%, 10%} (4) |

**EXP-53 registration** (2026-07-04, BEFORE implementation and BEFORE
any data contact beyond feature construction). The scheduled-flows +
new-trade-structure wave. Motivation: the campaign law — both survivors
(FOMC daily + hourly) are scheduled flows; every discovered price
pattern is dead. H-44/H-45 exploit the per-stock dividend calendar
already in the lake (`equities.market_corp_actions`); H-46 is the one
ML idea that changes the TRADE STRUCTURE (market-neutral residuals)
rather than the model; H-47/H-48 extend the calendar recipe to macro
prints and option-expiration flows. Screens: standard gauntlet
(2006-2018 dev, 1000 master-calendar permutations, BH within the wave,
2019-2026 untouched). H-44/H-45/H-46 on the 1000-name daily universe;
H-47/H-48 on SPY (mirroring the fomc_drift screen design).
Price-only-return caveat handled by construction: H-44 exits at the
cum-dividend close (no ex-day cash flow inside the window); H-45
enters at the ex-day close (post-drop).

| ID | Family | Grid |
|----|--------|------|
| H-44 | `dividend_runup` — long the `lead` trading days ending at the cum-dividend close (signal days [ex−1−lead, ex−2], earning cc into close(ex−1)) | lead {3, 5, 10} (3) |
| H-45 | `dividend_ex_drift` — long `hold` days from the ex-day close (post-drop recovery/drift) | hold {3, 5, 10} (3) |
| H-46 | `pca_residual_reversion` — trailing-252d log-return PCA refit every 21d; residual z = 21d cum-residual / (σ_res·√21); enter long z < −z_thr / short z > +z_thr, fixed 10d hold, both legs always on (market-neutral) | n_factors {1, 5} × z_thr {1.5, 2.0} (4) |
| H-47 | `macro_release_drift` — long the k trading days ending at the release-day close (CPI / NFP 08:30 ET prints; scheduled dates from BLS) | release {cpi, nfp} × k {1, 2, 3} (6) |
| H-48 | `opex_flows` — monthly option-expiration flow windows: the 4 trading days ending at the 3rd Friday (into_opex) vs the following 5 (post_opex), both directions registered | segment {into_opex, post_opex} × side {long, short} (4) |
