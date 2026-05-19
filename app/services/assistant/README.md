# assistant/

Conversational copilot over the platform's MCP tools. The developer
(today) and tenants (after SaaS lands) type natural language;
Claude plans, calls the system's tools, streams answers back grounded
in real platform data, and renders rich artifacts inline.

Full spec: [docs/assistant_plan.md](../../../docs/assistant_plan.md).

## Status

**Phase AS-1 — backend-only build.** Slice 1 is committed on
`feat/assistant-as1-slice1`. Slices 2–6 are not yet started.

| Slice | Status | Lands |
|---|---|---|
| 1. Scaffold + schemas + contract + env | ✅ committed (`feat/assistant-as1-slice1`) | shape only |
| 2. `service.py` + Anthropic + cache + prompts + models | ⏸ next | core loop |
| 3. `policy.py` + `runner.py` | ⏸ pending | tool dispatch |
| 4. CH tables + `store.py` | ⏸ pending | persistence |
| 5. `/cockpit/assistant/*` + SSE | ⏸ pending | HTTP surface |
| 6. Integration gate test | ⏸ pending | AS-1 done |

### Next-session pickup — Slice 2 punch list

**Goal:** the core LLM turn loop runs end-to-end against a mocked
Anthropic, with prompt caching markers in place and the response
cache deduplicating identical prompts.

Files to add:

| File | Purpose |
|---|---|
| `app/services/assistant/service.py` | `DefaultAssistantService` (real Anthropic SDK turn loop). Implements the `AssistantService` Protocol from `contract.py`. Tool-call dispatch is stubbed in this slice; the real `ToolRunner` lands in slice 3. |
| `app/services/assistant/cache.py` | `ResponseCache` — SQLite, keyed by `sha256(model + system_prompt_hash + tool_schema_hash + serialized_messages)`. Own DB file (`./.cache/assistant_responses.sqlite`); separate from the trading `LLMAgent` cache. |
| `app/services/assistant/models.py` | `ModelRegistry` — default `claude-sonnet-4-6`; `pick(use_extended_thinking=True)` returns `claude-opus-4-7`. Per-turn switch. |
| `app/services/assistant/prompts/__init__.py` | Loader that reads `v1.md` and computes its hash for cache-key inclusion. |
| `app/services/assistant/prompts/v1.md` | System prompt v1 (scope to platform questions, tool-grounded, refuse fabrication, ignore directives inside `<tool_result>` containers). |
| `tests/test_assistant_service.py` | Unit tests with a mock Anthropic client: one turn produces an assistant turn; tool-call iteration terminates; cost is summed. |
| `tests/test_assistant_cache.py` | Cache key determinism; hit/miss; prompt-hash bumping on system-prompt change. |
| `tests/test_assistant_models.py` | Registry returns expected ids; extended-thinking path picks Opus. |

Acceptance criteria for slice 2:
- `pytest tests/test_assistant_*.py` green (no real Anthropic calls).
- Prompt caching: `cache_control: {"type": "ephemeral"}` marker present on the system block and on the tool defs block.
- Cache-key hash includes the system-prompt-file hash; editing `prompts/v1.md` invalidates cached responses.
- Sonnet 4.6 default, Opus 4.7 selectable via `ContinueRequest.use_extended_thinking=True`.
- No silent failures: Anthropic errors become structured log lines + propagate as `AssistantStreamEvent(type=ERROR, ...)`.

Slices 3–6: see `docs/assistant_plan.md §15`. None of them block on
anything external; each is its own PR + tests + gate.

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
