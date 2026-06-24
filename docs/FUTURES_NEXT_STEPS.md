# Futures Lake — Next Steps

The futures lake is **production-ready for backtesting** as of 2026-06-23. This document tracks optional enhancements (all non-blocking; ranked by value).

## Completed (this session)

✅ Phase 1: byte-mirror flat files (88.85 GB, 2021-06-21→present, all 4 exchanges)
✅ Phase 2: `futures.polygon_raw` (179.2M rows, all contracts)
✅ Phase 3: `futures.polygon_continuous` (55.47M rows, 37 roots, volume roll + ratio adjust)
✅ Phase 4: read path wired (bars_gateway serves continuous; nightly refresh live)
✅ Nightly optimization: incremental (append daily, rebuild only on roll) — 60min → seconds on non-roll days
✅ Architecture documented (`docs/architecture/data_platform.md`)

---

## Remaining — Optional Enhancements

### 1. Parse session-aggregates (low effort, low priority)

**What:** Polygon flat files include `session_aggs_v1/` (daily bars) alongside `minute_aggs_v1/`. We only built the minute→raw→continuous path. Add daily parse.

**Why:** Completes the flat-file ingestion story; some consumers may prefer daily resolution over resampled 1-min.

**Where:**
- Mirror already captures session files (no new Polygon calls).
- Add `scripts/polygon_futures_parse_session_daily.py` (parallel to `polygon_futures_parse_raw.py`).
- Optional: new lake table `futures.polygon_daily` (or append to existing `schwab_futures_daily`).

**Effort:** 1–2 hours (reuse existing parse patterns)
**Impact:** Low (daily is niche vs 1-min; Schwab nightly already covers recent days)
**Owner:** —

---

### 2. Move nightly rebuild to CodeBuild (medium effort, medium priority)

**What:** The incremental nightly is light on non-roll days, but roll-day rebuilds (~1–2 min per root) currently run in the API's event loop (via `asyncio.to_thread`). Move to CodeBuild so the API stays responsive.

**Why:** Decouples heavy compute from the live server; scales independently.

**Prerequisites:**
- IAM grant: `stockalert-codebuild-silver-role` needs `glue:CreateDatabase` + `glue:PutTable` on the `futures` namespace (currently has equities only).
- Buildspec: already sketched in `scripts/codebuild/buildspec_futures_parse_raw.yml` (mirror + parse); create one for the nightly continuous rebuild.

**Where:**
- `app/services/ingest/nightly_futures_polygon_refresh.py`: add a mode to enqueue the rebuild to CodeBuild instead of running locally.
- Or: keep in-app as fallback, but default to CodeBuild for production.

**Effort:** 30 min code + IAM grant approval
**Impact:** Medium (API responsiveness, independent scaling)
**Owner:** (needs IAM approval)

---

### 3. Storage lifecycle — archive the mirror (low effort, low priority)

**What:** The byte-mirror (`polygon_flatfiles_mirror/`, 88 GB) + trades files are write-once, read-never after initial parse. Archive to S3 Intelligent-Tiering or Glacier to cut storage cost.

**Why:** ~$2–3/month cost reduction (small but free); data is durable on S3 and reproducible from lake if needed.

**Where:**
- `infra/` or `scripts/` — S3 lifecycle policy (transition after 30d to Intelligent-Tiering, after 90d to Glacier).
- Or: post-parse cleanup script.

**Effort:** 15 min (policy creation)
**Impact:** Low (cost only, no correctness impact)
**Owner:** —

---

### 4. Unify lake-fill on Athena (low effort, low priority)

**What:** Equities gap-fill already uses Athena `UNLOAD` (fast, serverless, scales); futures `lake_to_ch_fill` should use the same path instead of PyIceberg Python scans.

**Why:** Faster large-window backfills; leverages Athena's native SQL performance.

**Where:**
- `app/services/equities/lake_to_ch_fill.py` — the Athena pattern.
- `app/services/futures/lake_to_ch_fill.py` — port it.

**Effort:** 1 hour (mostly copy-paste with futures table names)
**Impact:** Low (already working; marginal speed gain on large fills)
**Owner:** —

---

### 5. Pre-warm ClickHouse for active watchlist (medium effort, low priority)

**What:** Off-hours (after nightly), load the active watchlist roots into CH so the first chart request of the day is instant (no lake-fill latency).

**Why:** Perceived latency; one less request blocks on S3.

**Where:**
- `ch_reconcile` (post-close) — extend to pre-cache today's active set.
- Or: standalone pre-warm task in the nightly loop.

**Effort:** 1–2 hours
**Impact:** Low (nice-to-have UX; already fast enough for interactive use)
**Owner:** —

---

### 6. ClickHouse High Availability (days of effort, low priority)

**What:** Self-hosted single-node CH can go down. Set up a replicated CH cluster or migrate to CH Cloud.

**Why:** Resilience for the hot tier (live charts unaffected if CH is down, but recent bars won't update; lake is ground truth).

**Where:**
- `infra/clickhouse.yaml` — replicate across nodes, or switch to managed CH Cloud.
- Operator/helm chart if Kubernetes is in the roadmap.

**Effort:** Days (architecture, networking, failover testing)
**Impact:** Low (not blocking for single-user backtest tool; lake is always available)
**Owner:** —

---

## Summary

**Ship now:** futures lake is production-ready.

**Recommended next (in order):**
1. #1 (session→daily) if you want daily ingestion complete.
2. #2 (CodeBuild) if the nightly ever scales (many roots or frequent rolls).
3. #3 (storage lifecycle) for cost.

**Nice-to-have:** #4–6 are valuable but not urgent.

---

## Links

- **Lake design:** [`docs/architecture_v2/`](../architecture_v2/README.md)
- **Platform overview:** [`docs/architecture/data_platform.md`](./architecture/data_platform.md)
- **Nightly script:** [`app/services/ingest/nightly_futures_polygon_refresh.py`](../app/services/ingest/nightly_futures_polygon_refresh.py)
- **Continuous rebuild:** [`scripts/polygon_futures_build_continuous.py`](../scripts/polygon_futures_build_continuous.py)
