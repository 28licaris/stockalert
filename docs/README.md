# StockAlert — documentation

Everything here describes the system **as it is**. Plans for shipped work live
in the commit history, not in this folder; when a doc and the code disagree,
**the code wins** and the doc is a bug.

Start with [`../CLAUDE.md`](../CLAUDE.md) for the repo map, then come here.

```
docs/
├── standards/      the rules — read before changing anything
├── architecture/   how the system is designed
├── operations/     how to run it
├── data/           the data platform: ingest, storage, read paths
├── trading/        the trading subsystem, models and signals
├── research/       validation methodology and findings
├── product/        customer-facing surfaces
├── reference/      vendor API docs (not ours)
├── ISSUES.md       live register — what is broken
└── TODO.md         live register — what is deferred
```

`ewt/` at the repo root holds Elliott Wave scan data — see "Research data at
the repo root" below.

Module-level documentation is **not here**: every functional package carries
its own `README.md` describing what it owns, its public contract, and how to
test it. Those are authoritative for code; this folder holds cross-module
design. See [`standards/doc_discipline.md`](standards/doc_discipline.md).

---

## [`standards/`](standards/README.md) — the rules

Non-negotiable. Read before writing code.

| Doc | When |
|---|---|
| [engagement.md](standards/engagement.md) | **Always.** Spec-first: restate, confirm, then write |
| [coding.md](standards/coding.md) | **Always.** No silent failures |
| [platform_design.md](standards/platform_design.md) | Any architectural call |
| [service_modules.md](standards/service_modules.md) | Editing `app/services/*` |
| [testing.md](standards/testing.md) | Writing tests |
| [trading_subsystem.md](standards/trading_subsystem.md) | Touching `sim/`, strategies or indicators |
| [doc_discipline.md](standards/doc_discipline.md) | Adding a module or changing a doc |
| [merge_discipline.md](standards/merge_discipline.md) | Merging any branch |
| [data/symbol_lifecycle.md](standards/data/symbol_lifecycle.md) | Adding a symbol; any ingest path |
| [data/timezone_et_vs_utc.md](standards/data/timezone_et_vs_utc.md) | Any trading-day math |
| [data/athena_dialects.md](standards/data/athena_dialects.md) | Writing Athena SQL |

## [`architecture/`](architecture/README.md) — how it is designed

| Doc | Covers |
|---|---|
| [README.md](architecture/README.md) | **The system map** — providers → ingest → storage → freshness → read |
| [lake_schema.md](architecture/lake_schema.md) | Column contracts, DDL, partitioning, S3 layout, maintenance |
| [providers.md](architecture/providers.md) | Provider abstraction, selection, adding one, token refresh |

## [`operations/`](operations/runbook.md) — how to run it

| Doc | Covers |
|---|---|
| [runbook.md](operations/runbook.md) | Restart, backfill, monitoring, disaster recovery |
| [startup_flow.md](operations/startup_flow.md) | What boot does; verifying each subsystem is alive |
| [commands.md](operations/commands.md) | Command cheatsheet |
| [deployment_dev.md](operations/deployment_dev.md) | **Spec (unbuilt)** — the AWS dev environment: one Graviton box, `api`/`worker` split, Cognito + Stripe test mode, cost |

## [`data/`](data/) — the data platform

| Doc | Covers |
|---|---|
| [adjusted_storage.md](data/adjusted_storage.md) | Why split adjustment is computed on read, not stored |
| [market_splits.md](data/market_splits.md) | The splits-only table and why it exists |
| [lake_read_layer.md](data/lake_read_layer.md) | The cold read path — `read_arrow`, source registry, unions |
| [symbol_onboarding.md](data/symbol_onboarding.md) | Hotload-on-add, read-path gap-fill, freshness |
| [futures_flatfile_mirror.md](data/futures_flatfile_mirror.md) | Polygon byte-mirror → raw → continuous |
| [options_ingestion.md](data/options_ingestion.md) | Schwab chain ingest, canonical contracts, GEX |
| [market_calendar.md](data/market_calendar.md) | Trading calendar and market events |

## [`trading/`](trading/) — subsystem, models, signals

| Doc | Covers |
|---|---|
| [subsystem_design.md](trading/subsystem_design.md) | The sim/backtest contract — Pydantic shapes, Protocols, structure |
| [indicators.md](trading/indicators.md) | Indicator registry + cross-timeframe sourcing |
| [elliott_wave.md](trading/elliott_wave.md) | The EW engine as built |
| [ppl.md](trading/ppl.md) | Posterior predictive levels — product spec |
| [ppl_mathematics.md](trading/ppl_mathematics.md) | PPL maths — GJR-GARCH + filtered historical simulation |
| [vol_premium.md](trading/vol_premium.md) | Vol premium ranking spec |
| [options_vol_premium_state.md](trading/options_vol_premium_state.md) | What options data covers; what is permanently gone |
| [sector_rotation.md](trading/sector_rotation.md) | RRG sector/theme rotation |
| [roadmap.md](trading/roadmap.md) | AI-trading roadmap (the update target for AI-trading changes) |

## [`research/`](research/) — validation and findings

| Doc | Covers |
|---|---|
| [research/mcpt_methodology.md](research/mcpt_methodology.md) | **The validation gate.** Permutation testing before any return claim |
| [hypotheses.md](research/hypotheses.md) | **Pre-register here before any sweep** |
| [findings.md](research/findings.md) | Canonical results. All pre-EXP-26 performance claims are dead |
| [platform.md](research/platform.md) | The R&D harness |
| [no_lookahead_audit.md](research/no_lookahead_audit.md) | Anti-cheating audit of every backtested strategy |
| [bayesian_alert_system.md](research/bayesian_alert_system.md) | Engine A (Bayesian posterior) design — **unbuilt**; Engine B shipped as PPL |

## [`product/`](product/) — customer-facing surfaces

| Doc | Covers |
|---|---|
| [identity_and_subscriptions.md](product/identity_and_subscriptions.md) | Customer identity, tenants, subscription model |
| [access_control.md](product/access_control.md) | Admin vs customer access, per-page policy |
| [api_contracts.md](product/api_contracts.md) | The API contract the SPA consumes |
| [assistant.md](product/assistant.md) | The chat copilot's interface + runtime |
| [news_alerts.md](product/news_alerts.md) | News + economic ingest → alerts |
| [brand.md](product/brand.md) | Brand/visual system |
| [customer_surfaces.md](product/customer_surfaces.md) | Home dashboard (shipped) + marketing landing (not built) |

## [`reference/`](reference/schwab-api/) — vendor docs

[`schwab-api/`](reference/schwab-api/) — Schwab's own API documentation.
**Not our architecture.** Kept for offline lookup when writing provider code.

---

## Research data at the repo root

`ewt/` — Elliott Wave consensus scan output: 18 ticker files, an index, and
structured extractions of analyst emails (ids, dates, symbols — not verbatim
newsletter text). Produced by the `ewt-email-scan` scheduled task, which lives
**outside this repo** at `~/.claude/scheduled-tasks/ewt-email-scan/`.

The task is mirrored at
[`../.claude/scheduled-tasks/`](../.claude/scheduled-tasks/README.md).
Partial by design: 30 of 496 analysis-bearing messages extracted as of
2026-08-05. `ewt/README.md` holds the backfill state and explains why
`state.json`'s watermark is deliberately `null` — the real watermark is
`processed_manifest.json`. Committed 2026-11-05; it had previously existed
only as untracked files in a single checkout.

## Adding or changing a doc

| Change | Update |
|---|---|
| New service / contract change | [`../CLAUDE.md`](../CLAUDE.md) layer map + that module's `README.md` |
| Storage / ingestion change | [`architecture/`](architecture/README.md), and [`data/`](data/) if it has its own spec |
| AI-trading change | [`trading/roadmap.md`](trading/roadmap.md) |
| Standards rule change | [`standards/`](standards/README.md) + its index entry |
| Operator procedure | [`operations/runbook.md`](operations/runbook.md) |
| Phase / gate / decision log | **the commit message** |

Three rules:

1. **No doc for unshipped work.** A spec for work that does not exist belongs
   in the PR that builds it, not here.
2. **One home per fact.** If something is true in two files, one of them will
   go stale. Link instead of restating.
3. **No `BUILD_JOURNAL.md`** — retired 2026-05-18. Commit messages are the
   journal.
