# assistant/

Conversational copilot over the platform's MCP tools. The developer
(today) and tenants (after SaaS lands) type natural language;
Claude plans, calls the system's tools, streams answers back grounded
in real platform data, and renders rich artifacts inline.

Full spec: [docs/assistant_plan.md](../../../docs/assistant_plan.md).

## Status

**Phase AS-1 — backend-only build.** Slices 1–3 are committed
on stacked branches. Slices 4–6 are not yet started.

| Slice | Status | Lands |
|---|---|---|
| 1. Scaffold + schemas + contract + env | ✅ committed (`feat/assistant-as1-slice1`) | shape only |
| 2. `service.py` + Anthropic + cache + prompts + models | ✅ committed (`feat/assistant-as1-slice2`) | core loop |
| 3. `policy.py` + `runner.py` | ✅ committed (`feat/assistant-as1-slice3`) | tool dispatch |
| 4. CH tables + `store.py` | ⏸ next | persistence |
| 5. `/cockpit/assistant/*` + SSE | ⏸ pending | HTTP surface |
| 6. Integration gate test | ⏸ pending | AS-1 done |

### Next-session pickup — Slice 4 punch list

**Goal:** persist conversation turns + tool calls to ClickHouse.
Currently all conversations live in an in-memory dict and turns
are lost on restart; slice 4 wires the durable CH-backed store.

Files to add:

| File | Purpose |
|---|---|
| `app/services/assistant/store.py` | `ConversationStore` — owner-scoped reads/writes against ClickHouse `assistant_conversations` + `assistant_turns` tables. |
| `tests/test_assistant_store.py` | Integration tests (require `clickhouse_ready` fixture): CRUD round-trip, tenant isolation, turn ordering. |

Files to modify:

| File | Change |
|---|---|
| `app/db/init.py` | Add CH DDL for `assistant_conversations` and `assistant_turns` tables. |
| `app/services/assistant/service.py` | Accept optional `store: ConversationStore`. Replace in-memory `_conv_index` with store calls. Persist `ConversationTurn` records (including `ToolCall` objects from slice 3) on each completed turn. |
| `app/services/assistant/__init__.py` | Re-export `ConversationStore`. |

Acceptance criteria for slice 4:
- `pytest tests/test_assistant_*.py -m "not integration"` green (unit tests unaffected).
- `pytest tests/test_assistant_store.py` green against a live CH instance.
- `load_conversation` returns the stored turns with correct ordering.
- `list_conversations` is owner-scoped — other tenant IDs never leak.
- A CH table scan confirms tool call records (name, args, result) are stored.

Slices 5–6: see `docs/assistant_plan.md §15`.

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
| `store.py` | 4 | `ConversationStore` — owner-scoped reads/writes against CH |
| `stream.py` | 5 | SSE event encoder |

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
