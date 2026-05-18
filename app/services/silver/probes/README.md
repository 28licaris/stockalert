# Provider adjustment probes

Universal framework for detecting **what adjustment status each
data provider returns** (raw / split-adjusted / fully-adjusted).

This matters because the silver medallion model assumes bronze stores
**what the provider sent**. If two providers send different
adjustment statuses for the same `(symbol, ts)` cell, naive
provider-precedence merging produces wrong silver prices. The probe
is how we detect this empirically — no guessing.

**Run it:**

```bash
poetry run python scripts/probe_provider_adjustment.py
# or pick a specific known split:
poetry run python scripts/probe_provider_adjustment.py --probe nvda_2024_10for1
# or write a JSON report:
poetry run python scripts/probe_provider_adjustment.py --out-json probe.json
```

All registered providers are tested against the **same** `ProbeSpec`
so the comparison is apples-to-apples.

---

## Architecture

```
ProbeSpec  ────►  ProviderAdjustmentProbe (Protocol)  ────►  list[ProbeResult]
(one known   (one implementation per provider —      (one row per
 split)       registered with @register_probe)         provider × endpoint × date)
```

- `base.py` — Protocol, `ProbeSpec`, `ProbeResult`, classifier,
  `KNOWN_PROBES` (the known-split library).
- `__init__.py` — registry, `register_probe()` decorator,
  `build_all_probes()`.
- `polygon.py` — Polygon's probe (REST adjusted=true/false +
  bronze.polygon_minute via PyIceberg).
- `schwab.py` — Schwab's probe (pricehistory daily).
- `scripts/probe_provider_adjustment.py` — operator runner; loads
  every registered probe, runs them, prints + JSON.

---

## Known-split library

Pre-curated probes covering well-known major US splits. Operator
selects one via `--probe NAME`; default is `aapl_2020_4for1`.

| Probe name | Stock | Split | Pre / Post date | Factor |
|---|---|---|---|---|
| `aapl_2020_4for1` (default) | AAPL | 4-for-1 forward | 2020-08-28 / 2020-08-31 | 4.0 |
| `nvda_2024_10for1` | NVDA | 10-for-1 forward | 2024-06-07 / 2024-06-10 | 10.0 |
| `amzn_2022_20for1` | AMZN | 20-for-1 forward | 2022-06-03 / 2022-06-06 | 20.0 |
| `googl_2022_20for1` | GOOGL | 20-for-1 forward | 2022-07-15 / 2022-07-18 | 20.0 |
| `tsla_2022_3for1` | TSLA | 3-for-1 forward | 2022-08-24 / 2022-08-25 | 3.0 |

Each probe is a `ProbeSpec` in `base.py` with both
expected-raw and expected-split-adjusted closes hard-coded from
Yahoo Finance.

Use older probes when a provider's history is deep; newer ones when
testing a provider with shallower history. **Don't remove old probes**
— they're regression checks if a provider ever changes its
adjustment behavior.

---

## How to add a new provider's probe

The whole point of this package is making provider onboarding
**purely additive**. Five steps; no changes to existing code:

### 1. Create `app/services/silver/probes/<provider>.py`

Implement the `ProviderAdjustmentProbe` Protocol. Use `polygon.py`
or `schwab.py` as a template.

```python
from app.services.silver.probes import register_probe
from app.services.silver.probes.base import (
    ProbeResult, ProbeSpec, classify, close_to,
)

@register_probe("my_new_provider")
class MyNewProviderProbe:
    provider_name = "my_new_provider"

    async def probe(self, spec: ProbeSpec) -> list[ProbeResult]:
        # Call the provider's API for daily bars in
        # [spec.pre_split_date, spec.post_split_date].
        # Return one ProbeResult per (endpoint, date).
        # NEVER raise — wrap exceptions into a ProbeResult with
        # classification="error" (so the runner produces a complete
        # report even when one provider misbehaves).
        ...
```

### 2. Import it from `probes/__init__.py`

Add the import to the bottom of `__init__.py` so the
`@register_probe` decorator fires when the registry is queried:

```python
from app.services.silver.probes import my_new_provider  # noqa: F401,E402
```

### 3. Run the probe

```bash
poetry run python scripts/probe_provider_adjustment.py
```

The new provider's rows appear automatically.

### 4. Record the finding

When the probe stabilizes (consistent verdict across multiple runs +
multiple known-splits), document the result in:
- `app/services/bronze/schemas.py` — add an `ADJUSTMENT_STATUS`
  constant alongside the new provider's bronze schema.
- `docs/silver_layer_plan.md` §2.9 — update the per-provider
  adjustment-status table.
- `docs/BUILD_JOURNAL.md` — decision-log entry with date + verdict.

### 5. Decide if the provider needs corp-actions ingest

**A provider needs corp-actions ingest if and only if:**
- It returns **raw** prices (`classification == "raw"`), AND
- Silver consumers need adjusted prices (they do — every consumer
  in our system reads `_adj` columns).

In that case, you need a corp-actions feed FROM SOME PROVIDER to
compute the adjustment. Today only Polygon publishes corp-actions
for us. Most retail brokers (Schwab, etc.) return split-adjusted
prices directly, so we don't need a separate corp-actions feed
from them — their adjustment is already baked in.

| Provider returns | Needs corp-actions ingest? |
|---|---|
| Raw | **YES** — silver build applies corp-actions to compute `_adj` columns |
| Split-adjusted | NO — silver build passes the provider's `_adj` straight through; computes `_raw` by un-adjusting via the corp-actions table |
| Fully adjusted (split + dividend) | NO — but consumers should know they're losing the "trader's eye view" raw |

Either way, **silver needs at least one provider in the precedence
list that supplies corp-actions** (so `silver.ohlcv_1m` `_adj`
columns are correct for whatever providers are configured).
Today: Polygon. After Polygon-pause: Schwab daily history is
already split-adjusted, but we lose the ability to *un-adjust* to
get clean `_raw` columns. The strategic mitigation is the
"maximize the seed universe before any Polygon pause" guidance
in [silver_layer_plan §9.7](../../../../docs/silver_layer_plan.md).

---

## Classification semantics

A `ProbeResult.classification` is one of:

| Value | Meaning |
|---|---|
| `raw` | Returned close is within tolerance of expected raw |
| `split_adjusted` | Returned close is within tolerance of expected split-adj |
| `other` | Returned a number but doesn't match either — investigate |
| `no_data` | Endpoint succeeded but returned no row for this date |
| `error` | Endpoint call raised (credentials missing, network error, etc.) |

Tolerances (from `base.py`): 50¢ absolute OR 0.5% relative,
whichever is larger. Catches normal provider rounding without
false-classifying.

The pre-split date is **diagnostic** (raw vs adjusted differ by the
split factor). The post-split date is a sanity check (raw == adjusted
from the split day forward, so both classifications match — that's
expected; we still record it so a single-date failure is obvious).

---

## When this probe should run

- **At provider onboarding** — once, when the bronze ingest lands.
  Result drives the bronze schema's `ADJUSTMENT_STATUS` constant.
- **At silver build CI gate** — every time silver_build runs in CI,
  the probe verifies provider behavior hasn't changed. If a verdict
  flips, fail the build before bad silver lands.
- **After any provider API version change** — re-probe to confirm
  the new API version behaves the same way.
- **When silver `bar_quality.disagreements` spikes** — a sudden
  cross-provider disagreement is often a provider silently changing
  adjustment behavior on a release.
