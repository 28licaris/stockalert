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
| H-15 | `fomc_drift` — long the pre-FOMC window | **REGISTERED-PENDING**: needs a historical FOMC meeting calendar (economic_data starts 2026-06); not runnable until a date backfill lands. Grid to be locked then. | pending data |
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
