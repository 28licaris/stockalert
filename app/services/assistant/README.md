# assistant/

Conversational copilot over the platform's MCP tools. The developer
(today) and tenants (after SaaS lands) type natural language;
Claude plans, calls the system's tools, streams answers back grounded
in real platform data, and renders rich artifacts inline.

Full spec: [docs/assistant_plan.md](../../../docs/assistant_plan.md).

## Status

**Phase AS-1 — slice 1 (this PR).** Scaffold only: schemas,
contract, env. No service implementation yet. Subsequent slices add
the concrete `AssistantService`, the tool runner + policy, the
ClickHouse store, the FastAPI/SSE routes, and the integration gate
test.

| Slice | Status | Lands |
|---|---|---|
| 1. Scaffold + schemas + contract + env | **this PR** | shape only |
| 2. `service.py` + Anthropic + cache + prompts + models | next | core loop |
| 3. `policy.py` + `runner.py` | | tool dispatch |
| 4. CH tables + `store.py` | | persistence |
| 5. `/cockpit/assistant/*` + SSE | | HTTP surface |
| 6. Integration gate test | | AS-1 done |

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

## Files (later slices)

| File | Lands | Owns |
|---|---|---|
| `service.py` | slice 2 | `DefaultAssistantService` — Anthropic SDK turn loop |
| `cache.py` | slice 2 | `ResponseCache` — SQLite, keyed by prompt hash |
| `models.py` | slice 2 | `ModelRegistry` — Sonnet 4.6 default, Opus 4.7 for `/explain` |
| `prompts/v1.md` | slice 2 | Versioned system prompt (hash is part of cache key) |
| `policy.py` | slice 3 | `ToolPolicy` + `DevModeToolPolicy` |
| `runner.py` | slice 3 | `ToolRunner` — MCP dispatch + §8.4 truncation |
| `store.py` | slice 4 | `ConversationStore` — owner-scoped reads/writes against CH |
| `stream.py` | slice 5 | SSE event encoder |

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
