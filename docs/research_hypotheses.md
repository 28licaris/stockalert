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
| H-1 | 2026-07-02 | `breakout_vol` daily (lookback {10,15,20,30,40,60} × vol_mult {1.0,1.25,1.5,2.0}), 1000-name clean universe, DEV 2022-23 screen + full-engine 2006-21 / 2024-25 | pooled PF (T1) / portfolio Sharpe (T2) | Tier-1 FAILED (p = 0.276, 1000 perms); Tier-2 running (EXP-37) | — |
| H-2 | 2026-07-02 | logistic ranker (13 as-of features), position-day top-50 dataset, split 2020-01-01 | holdout AUC vs label-permutation null | tested (EXP-38) | NOT significant: AUC 0.5552 vs null 0.4991±0.0344, **p = 0.058** — tilt at the luck boundary |

**EXP-39 signal battery** (registered 2026-07-02, BEFORE implementation
or any data contact; BH correction is across H-3..H-8 jointly). Screen:
Tier-1 in-sample MCPT, 1000-name clean universe, window **2006-01-01 →
2018-12-31**, primary metric pooled PF, 1000 permutations. 2019-2026
stays untouched for Tier-2 of survivors. All signals long-only,
close-to-close accounting.

| ID | Family | Grid |
|----|--------|------|
| H-3 | `meanrev_rsi` — RSI(n) oversold entry, strength exit | n {2,3,4} × entry {10,15,20,25,30} × exit {50,70} (30) |
| H-4 | `meanrev_zscore` — z of close vs SMA(n) entry, mean exit | n {10,20} × z {1.5,2.0,2.5} (6) |
| H-5 | `xsec_momentum` — cross-sectional top-bucket by trailing return, 21-bar rebalance | lookback {60,120,12-1} × bucket {decile,quintile} (6) |
| H-6 | `vol_compression` — ATR%ile squeeze arm + range break entry, range-low exit | atr-pctile {0.10,0.20} × breakout lookback {5,10} (4) |
| H-7 | `gap` — overnight gap trigger, fixed hold | direction {follow,fade} × gap {1,2,3}% × hold {1,3,5} (18) |
| H-8 | `high_52wk` — pullback low within prox of 52-wk high; exit at 2×prox distance | prox {2,5,10}% × pullback {3,5} (6) |

Notes:
- H-1 and H-2 are **retroactive registrations** of the already-selected
  EXP-33/EXP-36 candidates — the MCPT re-adjudication of pre-MCPT work.
  Their p-values must be read knowing the candidates were the survivors
  of many prior sweeps; a pass here is necessary, not sufficient. All
  NEW families get registered before first contact with the data.
- Closed tracks (do not re-register without new information sources):
  bar-pattern day-trading (DT-0..3), EW-as-momentum-gate (EXP-13..16),
  ranker as hard gate (EXP-31/35/36).
