# assistant/

Conversational copilot over the platform's MCP tools. The developer
(today) and tenants (after SaaS lands) type natural language;
Claude plans, calls the system's tools, streams answers back grounded
in real platform data, and renders rich artifacts inline.

Full spec: [docs/assistant_plan.md](../../../docs/assistant_plan.md).

## Status

**Phase AS-1 — backend-only build.** Slices 1–5 are committed.
Slice 6 (integration gate) is next.

| Slice | Status | Lands |
|---|---|---|
| 1. Scaffold + schemas + contract + env | ✅ committed (`feat/assistant-as1-slice1`) | shape only |
| 2. `service.py` + Anthropic + cache + prompts + models | ✅ committed (`feat/assistant-as1-slice2`) | core loop |
| 3. `policy.py` + `runner.py` | ✅ committed (`feat/assistant-as1-slice3`) | tool dispatch |
| 4. CH tables + `store.py` | ✅ committed (`feat/assistant-as1-slice4`) | persistence |
| 5. `/cockpit/assistant/*` + SSE | ✅ committed (`feat/assistant-as1-slice5`) | HTTP surface |
| 6. Integration gate test | ⏸ pending | AS-1 done |

### Next-session pickup — Slice 5 punch list

**Goal:** expose the assistant over HTTP with SSE streaming so callers
can stream turns from a web client or CLI.

Files to add:

| File | Purpose |
|---|---|
| `app/services/assistant/stream.py` | `encode_sse(event)` — encodes `AssistantStreamEvent` to `data: {...}\n\n` SSE wire format. |
| `app/api/routes_assistant.py` | FastAPI router: `POST /cockpit/assistant/conversations` (start), `POST /cockpit/assistant/conversations/{id}/turn` (continue, SSE response), `GET /cockpit/assistant/conversations` (list), `GET /cockpit/assistant/conversations/{id}` (load). |
| `tests/test_assistant_stream.py` | Unit tests for SSE encoder: JSON round-trip, delimiter format, event type wire values. |

Files to modify:

| File | Change |
|---|---|
| `app/main_api.py` | Mount the assistant router under `/cockpit`. |
| `app/services/assistant/__init__.py` | Re-export SSE encoder. |

Slices 6: see `docs/assistant_plan.md §15`.

## Distinct from the trading `LLMAgent`

This service is **interactive, user-driven, and cannot route orders**
by tool allowlist. The trading `LLMAgent` in
[`app/services/sim/strategies/llm_agent.py`](../sim/strategies/llm_agent.py)
is the *autonomous* per-bar trading agent — different caller,
different cadence, different output shape, different cache file.
See [assistant_plan.md §3.2](../../../docs/assistant_plan.md) for
the full table.

## Files (this slice)

| File | Owns |
|---|---|
| [schemas.py](schemas.py) | `Role`, `ToolCall`, `ConversationTurn`, `Conversation`, `AssistantStreamEvent`, `ContinueRequest`, `ConfirmRequest`, plus the dev-mode sentinels |
| [contract.py](contract.py) | `AssistantService` Protocol + `Principal` Protocol |
| [__init__.py](__init__.py) | Public re-exports |

## Files

| File | Slice | Owns |
|---|---|---|
| [schemas.py](schemas.py) | 1 | Wire contracts |
| [contract.py](contract.py) | 1 | `AssistantService` Protocol + `Principal` Protocol |
| [__init__.py](__init__.py) | 1+ | Public re-exports (updated each slice) |
| [service.py](service.py) | 2 | `DefaultAssistantService` — Anthropic SDK turn loop |
| [cache.py](cache.py) | 2 | `ResponseCache` — SQLite, keyed by prompt hash |
| [models.py](models.py) | 2 | `ModelRegistry` — Sonnet 4.6 default, Opus 4.7 for `/explain` |
| [prompts/v1.md](prompts/v1.md) | 2 | Versioned system prompt (hash is part of cache key) |
| [policy.py](policy.py) | 3 | `ToolPolicy` + `DevModeToolPolicy` — allowlist + write-tool flag |
| [runner.py](runner.py) | 3 | `MCPToolRunner` — MCP dispatch + §8.4 truncation |
| [store.py](store.py) | 4 | `ConversationStore` — owner-scoped reads/writes against CH |
| [stream.py](stream.py) | 5 | SSE event encoder (`encode_sse`, `event_stream`) |

## Design rules (per `feedback_service_module_design`)

- **Contract first.** Callers depend on `AssistantService`
  (Protocol), never the concrete class.
- **Owner-scoped reads.** Every storage query filters by
  `principal.tenant_id`. The lint check in
  [frontend_plan.md §7.2](../../../docs/frontend_plan.md) enforces
  this at PR time.
- **No silent failures.** Anthropic and MCP errors surface as `ERROR`
  events in the stream and as structured log lines. Pipefail in any
  shell helpers.
- **Result objects over raises.** Tool dispatch returns a typed
  `ToolCall.result` / `.error` shape; only programmer errors raise.
- **Lazy imports for heavy deps** (`anthropic`, `clickhouse_connect`)
  so importing this package stays cheap.

## Env vars

Set `ANTHROPIC_API_KEY` in `.env`. The Anthropic SDK auto-reads it;
the assistant does not duplicate the env-var name in `app/config.py`
(same pattern as the trading `LLMAgent`).

## Related docs

- [docs/assistant_plan.md](../../../docs/assistant_plan.md) — full
  spec, contracts, phasing.
- [docs/frontend_plan.md §5.13](../../../docs/frontend_plan.md) — the
  cockpit page/drawer that consumes this service.
- [docs/ARCHITECTURE.md §5.13](../../../docs/ARCHITECTURE.md) — the
  bounded-service entry.
