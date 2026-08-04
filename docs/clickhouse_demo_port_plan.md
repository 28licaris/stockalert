# Porting ideas from the ClickHouse FS Demo into StockAlert

**Status:** proposal / plan
**Source:** `clickhouse-fs-demo` (Node/Express + React demo showcasing ClickHouse for
capital-markets & payments). This doc maps its best ideas onto StockAlert's actual
(Python/FastAPI + ClickHouse + Iceberg + React/Vite) structure and lists the exact files
to create/modify.

The headline is **observability** (both *app/infra telemetry* and *LLM/agent telemetry*),
which StockAlert currently has **none** of. Secondary items — a server-driven chart
renderer and a vectorized SQL backtest core — are lower priority but high leverage.

---

## 0. Where StockAlert stands today (so we don't rebuild what exists)

| Concern | Demo | StockAlert today | Gap |
|---|---|---|---|
| CH query guardrails (readonly, row/mem/time caps) | `server/src/index.js` `READ_GUARDRAILS` | ✅ `app/services/clickhouse_query/query_service.py` (`_READ_ONLY_SETTINGS`) | **Already have it.** Only missing: per-query *stats capture* + workload tagging. |
| MCP/tool call wrapper | n/a (single `run_sql`) | ✅ `app/mcp/middleware.py` `tool_call()` — every tool wrapped, logs timing | Perfect **span injection point**, currently logs only. |
| Assistant / LLM loop | `server/src/agent.js` | ✅ `app/services/assistant/*` (runner, service, stream, policy, cache) | No tracing, no run-audit, no scores. |
| App/infra telemetry (OTel traces/metrics/logs) | `server/src/otel.js` → ClickStack | ❌ none | **Greenfield.** |
| LLM observability (Langfuse) | `server/src/agent.js` (traces + `agent_runs` + scores) | ❌ none | **Greenfield.** |
| Platform self-metrics from `system.*` | `server/src/metrics.js` | ⚠️ query_service hides `system.*`; no metrics endpoint | Small add. |
| Chart rendering | `web/src/components/Viz.jsx` (generic spec renderer) | ⚠️ purpose-built (`OhlcvChart`, `EquityTradeChart`, `lightweight-charts`) | Optional: add a spec renderer for assistant/screener output. |
| Vectorized SQL backtest | `server/src/backtest.js` | ⚠️ `app/api/routes_backtest.py` exists; `docs/no_lookahead_audit.md` | Optional: push the core into ClickHouse SQL. |

---

## Part A — Observability (do this first)

Two independent planes, both landing in ClickHouse so "the platform observes itself":

1. **App/infra telemetry** — OTel traces + metrics + logs from FastAPI, every ClickHouse
   query, and every MCP tool call.
2. **LLM/agent telemetry** — Langfuse traces of the assistant + a ClickHouse `assistant_runs`
   audit table + automated quality scores.

### A1. App/infra telemetry via OpenTelemetry → ClickHouse (ClickStack)

**Concept (demo `server/src/otel.js`):** a bootstrap module loaded *before* the app that
(a) is a no-op unless `OTEL_ENABLED=1`, (b) names each process as its own `service.name`,
(c) auto-instruments the web framework + all outbound HTTP so every DB query becomes a span,
and (d) bridges the language's logging into the OTel logs pipeline with a noise filter.

**Python translation.** `clickhouse-connect` uses `urllib3`, not `httpx`, so the demo's
"every HTTP call is a span" trick needs `opentelemetry-instrumentation-urllib3` **plus** a
thin manual span in the DB client (richer: attach SQL, `rows_read`, `elapsed`).

**Dependencies** (`pyproject.toml`):
```
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-grpc     # OTLP → collector on :4317
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-urllib3       # captures clickhouse-connect HTTP
opentelemetry-instrumentation-logging       # trace-id correlation in log records
```

**New files:**
- `app/observability/__init__.py`
- `app/observability/otel.py` — the bootstrap. Mirrors `otel.js`:
  - `init_telemetry()` returns early unless `settings.otel_enabled`.
  - `Resource(service.name=...)` — `stockalert-api`, `stockalert-worker`, etc. (per process).
  - `FastAPIInstrumentor`, `URLLib3Instrumentor`, `LoggingInstrumentor` wired up.
  - `OTLPSpanExporter(endpoint=settings.otel_endpoint)` + `BatchSpanProcessor`.
  - A **logging→OTel bridge** (`LoggingHandler` from `opentelemetry.sdk._logs`) attached to
    the root logger, with a filter dropping retried transport errors (demo `otel.js:71`);
    overridable via `OTEL_LOG_ALL=1`.
- `app/observability/spans.py` — tiny helpers: `@traced` decorator + `span(name, **attrs)`
  context manager for hand-instrumenting hot paths.

**Modify:**
- `app/config.py` — add `otel_enabled`, `otel_endpoint`, `otel_service_name`, `otel_log_all`.
- `app/main_api.py` — call `init_telemetry()` at the **top of the lifespan** (before
  `_safe_start` subsystems) — or, cleaner, run uvicorn under `opentelemetry-instrument` and
  keep `otel.py` for the logging bridge + manual spans only.
- `app/db/client.py` — wrap the execute/query path in a CLIENT span carrying
  `db.system=clickhouse`, `db.statement` (truncated), and post-run `rows_read`/`elapsed`
  from the CH response summary. This is the single most valuable span in the system.
- `app/mcp/middleware.py` — **turn `tool_call()` into a span emitter.** It already brackets
  every tool with timing; wrap its body in `tracer.start_as_current_span(f"mcp.tool.{name}")`
  and set `error.type` on the except branches. The docstring already lists "cost accounting"
  as a future hook — this is that hook. **One edit instruments all ~24 MCP tools at once.**
- `app/services/assistant/runner.py` — each `MCPToolRunner.run` dispatch becomes a child span,
  so an assistant turn shows as: assistant span → N tool spans → N ClickHouse spans.

**Collector + storage** (`infra/` — StockAlert already has an `infra/` dir):
- `infra/otel-collector-config.yaml` — copy the demo's `payments-observability/collector/
  otel-collector-config.yaml`: OTLP receiver → `clickhouse` exporter → `otel_traces`/
  `otel_logs`/`otel_metrics`, `memory_limiter` + `batch` (coalesce before MergeTree),
  **no sampling**.
- `docker-compose.yml` — add a `clickhouse/clickstack-otel-collector` service (profile `otel`)
  writing into the existing ClickHouse. Read traces with self-hosted **HyperDX** or plain SQL.

**Payoff:** one screen (HyperDX or a SQL tile) showing an assistant question fanning out into
MCP tool calls and the exact ClickHouse queries each ran, with latencies — the demo's single
most compelling observability moment, and it drops onto StockAlert's existing call graph with
~4 real edits.

### A2. LLM/agent observability (Langfuse + `assistant_runs` audit table)

**Concept (demo `server/src/agent.js`):** every agent run is (1) traced to Langfuse — one
trace, per-turn `generation`s linked to a version-managed prompt, per-tool `span`s with
`rows_read`/`elapsed`, plus **automated server-side scores** (`sql-success`, `answered`,
`tool-calls`) and thumbs feedback — and (2) inserted into a ClickHouse `agent_runs` table so
you can *audit the AI with SQL*.

**Dependencies:** `langfuse` (Python SDK). Optional & lazy — only active when keys are set
(demo `agent.js:51-69`).

**New files:**
- `app/observability/llm_trace.py` — thin Langfuse wrapper: `trace(name, input, session_id,
  user_id, tags)`, `generation(...)`, `span(...)`, `score(...)`. No-op when unconfigured, so
  tests and local runs are unaffected.
- `app/db/assistant_runs_repo.py` — insert + read helpers for the audit table.
- `migrations/` — DDL for `assistant_runs`:
  ```sql
  CREATE TABLE IF NOT EXISTS assistant_runs (
    run_id UUID, ts DateTime64(3), user_id String, session_id String,
    question String, model String,
    tool_calls Array(String), n_tool_calls UInt16,
    rows_returned UInt32, latency_ms UInt32,
    input_tokens UInt32, output_tokens UInt32,
    trace_id String, status String, error String
  ) ENGINE = MergeTree ORDER BY ts;
  ```

**Modify:**
- `app/services/assistant/service.py` — at the `client.messages.stream(**kwargs)` call site
  (service.py:202) and the streaming client (service.py:417), open a Langfuse `generation`
  around each turn and record token usage from the final message. Start the run-level `trace`
  in the assistant entrypoint; end it after the loop.
- `app/services/assistant/runner.py` — record each tool dispatch as a Langfuse `span`
  (input = tool + args, output = `row_count`/`elapsed_s` already on `ToolResult`).
- After the loop: compute automated scores + `INSERT` the `assistant_runs` row (fire-and-forget,
  never blocks the response — demo flushes *after* the client's `done`).
- assistant routes — add `POST /api/assistant/score` for thumbs-up/down → Langfuse `score`
  (demo `agent.js:504`).
- **Prompt-management-as-source-of-truth** (optional, powerful): fetch the `production`-labelled
  prompt from Langfuse with the in-repo prompt as fallback (demo `agent.js:200-255`), so
  `app/services/assistant/prompts/` becomes the fallback, not the only source.

**Payoff:** `SELECT question, n_tool_calls, latency_ms, input_tokens+output_tokens AS tokens
FROM assistant_runs ORDER BY ts DESC LIMIT 20` — instant compliance/quality trail for a
trading assistant, plus filterable quality in the Langfuse UI. This is the "observe the AI"
story you liked, applied to your own assistant.

### A3. Platform self-metrics from `system.*`

**Concept (demo `server/src/metrics.js`):** live platform metrics sourced entirely from
ClickHouse `system.*` — table rows/bytes, per-column compression, part pressure, live merges,
recent `query_log` — so the app reports its own storage/health truthfully.

**New file:**
- `app/services/observability_metrics.py` — read-only queries against `system.tables`,
  `system.parts`, `system.columns` (compression ratio), `system.merges`, `system.query_log`.
  Note: `query_service.py` deliberately **hides** `system.*` from the cockpit — keep that;
  this is a separate, curated, server-owned endpoint, not ad-hoc access.

**Modify:**
- `app/api/routes_health.py` (or a new `routes_observability.py`) — expose
  `GET /api/observability/storage`, `/merges`, `/recent-queries`.
- Frontend: a small "Platform" panel (see the demo's Data Ops tab) — optional.

**Reusable trick worth copying:** tag ad-hoc/assistant queries with a `query_id` **prefix**
(e.g. `asst_<uuid>`) so `system.query_log`/`system.processes` can attribute load by workload
class (demo `metrics.js` LoadSampler + `clickhouse.js` `queryWithStats(..., queryId)`). Add an
optional `query_id` param to `app/db/client.py`'s execute path and set it from the assistant.

---

## Part B — Other high-value concepts (after observability)

### B1. Server-driven viz specs + a generic renderer

**Concept (demo `web/src/components/Viz.jsx` + `server/src/agent.js`):** the **backend** returns
`{type, ...columnMappings}` (e.g. `{"type":"line","x":"ts","y":"close"}`) and one generic
`<Viz>` renders ~20 chart types with no per-answer frontend code. The same spec flows through
assistant answers, dashboard tiles, and query results — so an AI answer becomes a pinnable
chart for free.

**StockAlert fit:** you already have `lightweight-charts` for OHLCV and a chat panel, but charts
are purpose-built. A spec renderer lets the **assistant** and **screener/signals** results draw
themselves.

**New files:**
- `frontend/src/components/viz/Viz.tsx` — dispatch on `spec.type`. Reuse `lightweight-charts`
  for `candles`/`line`; hand-roll SVG for `bars`/`pareto`/`slo-board`/`kpis`/`heatmap`
  (port from `web/src/components/Viz.jsx`).
- `frontend/src/components/viz/types.ts` — the `VizSpec` union (shared contract).
- `app/services/assistant/viz.py` — a `sanitize_viz()` allowlist (demo `agent.js:280`) so a bad
  spec degrades to a table.

**Modify:**
- `app/services/assistant/schemas.py` / `contract.py` — let a tool result / final answer carry
  an optional `viz` spec.
- `frontend/src/components/chat/ChatMessage.tsx` — render `<Viz>` when a message has a spec, else
  the existing table (`ResultsTable.tsx`).
- `frontend/src/components/clickhouse/ResultsTable.tsx` — add a chart/table toggle.

### B2. Vectorized, no-lookahead SQL backtest core

**Concept (demo `server/src/backtest.js`):** the whole backtest — parameter sweep + walk-forward
train/test — runs in **one pass** over the bars MV using `groupArray` → `arrayCumSum` prefix
sums → array-lambda signal/position/PnL, with no-lookahead baked in
(`arrayPushFront(arrayPopBack(want), 0)`) and per-flip transaction costs. Directly relevant to
`docs/no_lookahead_audit.md` and `docs/mcpt_methodology.md`.

**StockAlert fit:** if backtests currently pull bars into Python, moving the vectorized core into
ClickHouse is a large speedup and the walk-forward query is a ready-made overfit detector.

**Modify:**
- `app/api/routes_backtest.py` / the backtest service — add a "SQL engine" path that composes the
  vectorized query against your `bars` schema (adapt `arraysCTE`/`sigCols`/`metricsCols`).
- `docs/` — cross-link to `no_lookahead_audit.md`; the `arrayPushFront(arrayPopBack(...))` shift is
  the audit made executable.

### B3. Small, cheap wins

- **`max(ts)`-anchored live windows** (demo `web/src/lib/live.js`): anchor tile windows to
  `(SELECT max(ts) FROM t)` not `now()`, so charts freeze on last real data instead of blanking
  during market-hours gaps. Apply in `app/services/live/*` read queries + frontend live hooks.
- **CH client resilience** (demo `server/src/clickhouse.js`): async-insert batching with
  transient-TLS retry, and client recycling to rebalance after scale-out — relevant once ingest
  scales. Compare against `app/db/batcher.py`.
- **Guardrail stats capture:** have `query_service.py` return CH `summary` (`rows_read`,
  `elapsed`, `bytes_read`) alongside rows so the cockpit + assistant can show "scanned X rows in
  Y ms" (demo `queryWithStats`).

---

## Suggested sequencing

| Phase | Scope | Rough effort |
|---|---|---|
| **1** | A1 traces: `otel.py`, config, DB-client span, `tool_call` span, collector + compose | ~1 day |
| **2** | A2 LLM obs: `llm_trace.py`, `assistant_runs` table + repo, wire assistant service/runner, score route | ~1 day |
| **3** | A1 logs bridge + A3 self-metrics endpoint + query_id workload tagging | ~half day |
| **4** | B1 viz spec renderer (assistant + cockpit) | ~1–2 days |
| **5** | B2 SQL backtest core, B3 polish | as needed |

Phases 1–2 deliver the observability you asked for and are independent of everything else.

---

## File-by-file checklist

### Demo files to read as reference (in `clickhouse-fs-demo`)
- `server/src/otel.js` — OTel bootstrap, per-process service name, log bridge + noise filter.
- `payments-observability/collector/otel-collector-config.yaml` — collector → ClickHouse.
- `payments-observability/lib/deeplink.js` — "Investigate in HyperDX" URL builder (nice-to-have).
- `server/src/agent.js` — Langfuse traces/generations/spans/scores + `agent_runs` audit + prompt mgmt.
- `server/src/metrics.js` — `system.*` self-metrics + `query_id` workload attribution.
- `server/src/clickhouse.js` — `queryWithStats`, async-insert retry, client recycling.
- `web/src/components/Viz.jsx` — generic chart renderer (spec → SVG).
- `server/src/backtest.js` — vectorized no-lookahead backtest SQL.

### StockAlert files to CREATE
- `app/observability/__init__.py`
- `app/observability/otel.py`
- `app/observability/spans.py`
- `app/observability/llm_trace.py`
- `app/services/observability_metrics.py`
- `app/db/assistant_runs_repo.py`
- `migrations/NNN_assistant_runs.sql`
- `infra/otel-collector-config.yaml`
- `frontend/src/components/viz/Viz.tsx` *(Part B)*
- `frontend/src/components/viz/types.ts` *(Part B)*
- `app/services/assistant/viz.py` *(Part B)*

### StockAlert files to MODIFY
- `pyproject.toml` — OTel + `langfuse` deps.
- `app/config.py` — OTel/Langfuse settings.
- `app/main_api.py` — `init_telemetry()` in lifespan.
- `app/db/client.py` — ClickHouse CLIENT span + stats capture + optional `query_id`.
- `app/mcp/middleware.py` — `tool_call()` emits a span (instruments all MCP tools at once).
- `app/services/assistant/runner.py` — tool-dispatch spans (OTel + Langfuse).
- `app/services/assistant/service.py` — run-level trace + per-turn generation + token usage.
- `app/services/assistant/schemas.py` / `contract.py` — optional `viz` on results *(Part B)*.
- `app/api/routes_health.py` (or new `routes_observability.py`) — self-metrics endpoints.
- assistant routes — `POST /api/assistant/score`.
- `docker-compose.yml` — collector service (profile `otel`).
- `frontend/src/components/chat/ChatMessage.tsx`, `clickhouse/ResultsTable.tsx` — render `<Viz>` *(Part B)*.

---

## Key adaptation notes (Node → Python gotchas)

1. **`clickhouse-connect` uses `urllib3`, not `httpx`** — CH queries won't auto-span via httpx
   instrumentation. Use `opentelemetry-instrumentation-urllib3` **and** a manual span in
   `app/db/client.py` (the manual span is what carries SQL + `rows_read`).
2. **StockAlert already enforces guardrails** — do **not** re-add them; just capture the CH
   response `summary` for stats.
3. **Keep telemetry optional & lazy** (demo pattern): no keys / `OTEL_ENABLED` unset ⇒ no-op, so
   local dev and the test suite are untouched. Respect the existing `_safe_start` isolation —
   telemetry init must never take the process down.
4. **`tool_call()` is the highest-leverage single edit** — it already wraps every MCP tool, so
   one span there lights up the whole agent tool surface.
