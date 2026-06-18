# AI chat interface — design & operations

How the cockpit's AI chat panel works end-to-end: the browser, the server,
the model, and the data round-trip between them. Plus what to harden before
production and which model architectures to weigh when we revisit this.

This is the **interface / runtime** view. For the backend service internals
(slices, schemas, tool policy, storage DDL) see
[assistant_plan.md](assistant_plan.md) and
[app/services/assistant/README.md](../app/services/assistant/README.md). For
the frontend component map see
[frontend/src/components/chat/README.md](../frontend/src/components/chat/README.md).

**Status:** backend AS-1 complete; chat panel shipped (frontend-only, consumes
the existing API). Live responses need `ANTHROPIC_API_KEY` set on the server.

---

## 1. Requirements

### Functional

- A collapsible chat panel on the right of the dashboard; toggled from the
  topbar or `⌘/Ctrl+I`; open-state persists per user; persists across routes.
- Natural-language Q&A grounded in **real platform data** — the assistant can
  call the server's read-only tools (live bars, signals, screener, indicators,
  coverage, backtests, corp-actions, lake queries…) to answer.
- Responses **stream** token-by-token; tool activity is visible as it happens;
  each turn shows model + token + cost accounting.
- Conversations are persisted server-side (owner-scoped).

### Non-functional

- **The model never touches the database directly** — it requests tool calls;
  the server runs them and returns only the (truncated) results.
- **The API key never leaves the server.** The browser only ever sees a text +
  tool-activity stream.
- Tools are **read-only** by policy; state-mutating tools are blocked (and, when
  enabled, gated behind explicit user confirmation).
- Cheap and bounded: capped tool-loop iterations, capped output tokens, prompt
  caching, and a response cache for repeat text-only turns.
- Every seam for multi-tenant SaaS (auth principal, per-tenant quota, audit) is
  present today as a no-op, populated by middleware later.

---

## 2. Runtime architecture — the round-trip

See the rendered data-flow diagram in the chat history (`assistant_chat_dataflow`).
In words:

```
Browser (chat panel)  ──①  POST /turn (SSE opens) ──▶  Your server (FastAPI + AssistantService)
                      ◀──⑤  SSE: text + tool chips ──

                                Your server  ──②  prompt + tool schemas ──▶  Anthropic API (Claude)
                                             ◀──③  reply: text + tool_use ──
                                                   (steps ②–④ loop, ≤10×)

                                Your server  ──④  run tool ──▶  MCP tools ──▶  ClickHouse + S3 lake
                                             ◀──    result   ──            ◀──

                                Your server  ──⑥  persist turn ──▶  ClickHouse (assistant_turns)
```

1. **Browser → server.** `useChatStore.send()` lazily creates a conversation
   (`POST /cockpit/assistant/conversations`, once), then `POST`s the turn to
   `/conversations/{id}/turn` and reads the response as a stream.
2. **Server → Claude.** `DefaultAssistantService` builds the message history
   plus the **schemas** of the allowed tools and calls the Anthropic SDK with a
   cache-marked system prompt.
3. **Claude → server.** Claude streams text and, when it needs data, emits
   `tool_use` requests.
4. **Server runs the tool.** `MCPToolRunner` dispatches to the in-process MCP
   tool, which queries ClickHouse / the S3 lake; the truncated result is fed
   back to Claude. Steps ②–④ repeat until Claude is done (hard cap 10).
5. **Server → browser (SSE).** Throughout, the server emits Server-Sent Events
   the panel folds into the message.
6. **Persist.** The completed turn is written to `assistant_turns` (ClickHouse).

### Why these choices

| Decision | Rationale |
|---|---|
| Agent loop runs **server-side** | Key stays server-side; the browser is a thin renderer; tool access is centrally policed. |
| **SSE** (not WebSockets) | A turn is one-directional streaming (server→browser). SSE over `fetch` is the simplest fit. |
| Hand-written FE client | `openapi-fetch` can't stream and the assistant routes aren't in the openapi codegen. |
| **Zustand** for chat state | A streaming turn mutates one message incrementally — doesn't fit React Query's cache lifecycle. |
| `LLMClient` **Protocol** seam | The provider (Anthropic SDK) sits behind one interface — see §6, the swap point for alternative models. |

---

## 3. The conversation API (what the frontend calls)

Base: `/cockpit/assistant` (same-origin). Full schemas in
`app/services/assistant/schemas.py`.

| Method · path | Purpose | Shape |
|---|---|---|
| `POST /conversations` | start a conversation | → `Conversation` (JSON) |
| `GET /conversations` | list (owner-scoped) | → `Conversation[]` |
| `GET /conversations/{id}` | header + full turn history | → `{conversation, turns}` |
| `POST /conversations/{id}/turn` | **stream one turn** | body `ContinueRequest` → `text/event-stream` |
| `DELETE /conversations/{id}` | cancel in-flight turn | → `{cancelled}` |
| `POST /conversations/{id}/confirm` | approve/deny a write tool | **501 until AS-2** |

### SSE event contract

Each frame is `data: {"type": <StreamEventType>, "payload": {...}}`. The panel
handles:

| Event | Payload | UI effect |
|---|---|---|
| `text_delta` | `text` | append to the answer |
| `tool_call_started` | `name`, `call_id`, `args` | add a running tool chip |
| `tool_result` / `tool_error` | `call_id`, `error?`, `elapsed_s` | resolve the chip ok/error |
| `turn_completed` | `model`, `tokens_in/out`, `cost_usd`, `cache_hit` | cost/token footer |
| `error` | `kind`, `message` | inline error |
| `done` | — | stream ends |

Not yet surfaced (room to grow): `thinking_delta`, `tool_call_pending_confirm`,
`artifact_ready`, `quota_warning`.

---

## 4. Security & trust boundaries

- **Secret isolation.** `ANTHROPIC_API_KEY` is read by the server only
  (`AnthropicLLMClient`). It is never exposed to the browser or the model.
- **Read-only tools.** `DevModeToolPolicy` allows only read tools; write tools
  (e.g. `run_backtest`) are blocked. The `pending_confirm` → `confirm` flow
  (AS-2) is the gate for any future mutating tool.
- **Result truncation.** Tool results are clipped (lists → 50 items, JSON → 5k
  chars) before going to the model — bounds tokens and accidental data
  exfiltration breadth.
- **Tenant isolation seam.** Conversations/turns are `owner_id`-scoped in
  ClickHouse today (dev principal hardcoded); real auth middleware injects the
  `Principal` in SaaS mode with no schema change.
- **Prompt-injection surface is small today** because every tool reads *our own*
  database. It grows the moment a tool ingests external/free text (web, news,
  user uploads) — see §6.

---

## 5. Cost & persistence

- **Model:** `claude-sonnet-4-6` default; `claude-opus-4-7` when extended
  thinking is requested (`ModelRegistry`). Output capped at 8k tokens.
- **Prompt cache:** the system prompt is sent with an Anthropic ephemeral cache
  marker → 5-minute reuse across turns with identical (system, model, tools).
- **Response cache:** text-only turns are cached in SQLite keyed by a hash of
  (model, system, tools, messages); tool-assisted turns are never cached.
- **Persistence:** `assistant_conversations` (ReplacingMergeTree, upsert header)
  + `assistant_turns` (append-only) in ClickHouse — also the audit log.

---

## 6. Production readiness — what to harden

The system is architecturally production-shaped (clean seams, owner scoping,
read-only policy). Before real users, address:

**Identity & access**
- Replace the dev `Principal` with real auth middleware (the seam exists).
  Verify owner-scoping on every read (already enforced in `store.py`).
- Per-tenant **tool allowlist by plan tier** (policy seam is a no-op today).

**Cost & abuse control**
- Enforce per-tenant **token / $ / tool-call budgets** (quota seam is a no-op).
  Surface `quota_warning` in the UI and return `429` past the ceiling.
- Per-turn and per-conversation cost ceilings in addition to the 10-iteration
  and 8k-token caps.
- Rate-limit `POST /turn` per principal.

**Reliability**
- **Timeouts** on both model calls and tool calls; retry/backoff on Anthropic
  `429`/`5xx`; consider a fallback model on outage.
- **Stream resume**: SSE drops mid-turn lose the response. `client_request_id`
  is the idempotency seam — wire reconnect/resume. `DELETE …` cancel is wired.
- Ensure tool calls to ClickHouse/lake are **non-blocking** (async/threaded) so
  one slow query doesn't stall the event loop under concurrency. Long-lived SSE
  holds a worker per active turn — size the async pool accordingly.

**Privacy & compliance**
- Conversation text + tool results are sent to Anthropic. For tenants, disclose
  this; consider PII scrubbing and Anthropic's zero-retention options. (Anthropic
  does not train on API traffic by default.)
- Since we're **AWS-only**, **Claude via Amazon Bedrock** is the natural prod
  path: same models, in-VPC, IAM auth (no raw API key), data-residency controls.
  The `LLMClient` seam makes it a drop-in (§7, option D).

**Prompt-injection hardening** (when tools touch external text)
- Treat tool output as untrusted; never let it escalate tool permissions; keep
  write tools behind explicit confirm; consider an output filter.

**Observability**
- Metrics: turn latency (p50/p95), tokens/$ per turn, tool latency + error
  rate, cache-hit rate, model error rate. Dashboards + alerts on cost spikes and
  error-rate regressions. `assistant_turns` is the durable audit trail.

**Frontend polish**
- Resume conversations on reload + a conversation switcher (history is already
  stored server-side); virtualize long transcripts; mobile drawer; a11y pass.
  `react-markdown` renders without raw HTML (no XSS), keep it that way.

**Testing**
- Backend AS-1 gate tests exist. Add frontend unit tests for the SSE parser and
  the store reducer, plus one e2e smoke (start → stream → render).

---

## 7. Alternative model architectures (options for next time)

The single swap point for all of these is the **`LLMClient` Protocol** in
`app/services/assistant/service.py` (and `ModelRegistry` for routing). Today
exactly one implementation (`AnthropicLLMClient`) sits behind it.

| Option | What | Pros | Cons / when |
|---|---|---|---|
| **A. Anthropic API** *(current)* | Managed Claude over HTTPS | Best tool-use/reasoning; prompt caching; zero infra | Per-token cost; data leaves the network; external dependency |
| **B. Claude via AWS Bedrock** | Same models, in our AWS account | In-VPC, IAM auth, data-residency/compliance; no raw key; **we're already AWS-only** | Slightly different SDK; regional model availability. **Recommended prod path.** |
| **C. Self-hosted open-weight** | Llama / Qwen / Mistral behind vLLM/TGI/Ollama (OpenAI-compatible) | Data never leaves our infra; fixed GPU cost, no per-token; full control | Weaker agentic tool-use than frontier; GPU ops burden; tool-call format differs (translate at the seam) |
| **D. Managed open-weight** | Open models via Bedrock / Vertex / Together / Fireworks | No GPU ops; data-residency options; cheaper than frontier | Still weaker tool-use; another vendor |
| **E. Hybrid routing** | Cheap/local for simple turns, frontier for hard ones | Cost/latency optimization | Routing logic + eval to decide "hard"; two providers to maintain. `ModelRegistry` is the seam. |
| **F. Fine-tuned small model** | Fine-tune on our own `assistant_turns` tool-use traces | Cuts cost on the common case; domain-tuned | Training/eval pipeline; later-stage; pairs well with E |

Orthogonal add-on (any option): a **RAG / long-term memory layer** (vector store
over docs + past conversations) for grounding and recall across sessions.

**Guidance for when we revisit:** start at **B (Claude on Bedrock)** for the
production cutover — it keeps frontier quality while satisfying the AWS-only,
in-VPC, no-key-in-env posture with the least change. Reach for **C/D** only if
data-egress policy forbids any external model or per-token cost at scale
dominates; reach for **E/F** as cost optimizations once usage patterns are known.

---

## 8. References

- [assistant_plan.md](assistant_plan.md) — backend master plan (slices, tool
  policy, storage DDL, phasing, guardrails).
- [app/services/assistant/README.md](../app/services/assistant/README.md) —
  backend file map + status.
- [frontend/src/components/chat/README.md](../frontend/src/components/chat/README.md)
  — frontend component map + SSE contract.
- [frontend_plan.md](frontend_plan.md) — the cockpit shell + Principal/quota/audit seams.
