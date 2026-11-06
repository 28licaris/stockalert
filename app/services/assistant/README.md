# assistant/

Conversational copilot over the platform's MCP tools. The user types natural
language; Claude plans, calls the system's tools, streams answers back grounded
in real platform data, and renders rich artifacts inline.

Chat surface + UX contract:
[`docs/product/assistant.md`](../../../docs/product/assistant.md).

## What this owns

The interactive, user-driven assistant — and **nothing that routes orders**,
enforced by tool allowlist rather than by convention.

Distinct from the trading `LLMAgent` in
[`app/services/sim/strategies/llm_agent.py`](../sim/strategies/llm_agent.py),
which is the *autonomous* per-bar trading agent: different caller, different
cadence, different output shape, different cache file.

## Layout

| File | Owns |
|---|---|
| [schemas.py](schemas.py) | `Role`, `ToolCall`, `ConversationTurn`, `Conversation`, `AssistantStreamEvent`, `ContinueRequest`, `ConfirmRequest` |
| [contract.py](contract.py) | `AssistantService` Protocol + `Principal` Protocol — the public interface |
| [service.py](service.py) | `DefaultAssistantService` — the Anthropic SDK turn loop |
| [cache.py](cache.py) | `ResponseCache` — SQLite, keyed by prompt + model + system-prompt hash |
| [models.py](models.py) | `ModelRegistry` — default vs extended-thinking model choice |
| [prompts/](prompts/README.md) | Versioned system prompts (`v1.md`, `v2.md`, …) behind a registry — `current()` resolves the active version, whose hash is part of the cache key |
| [policy.py](policy.py) | `ToolPolicy` + `DevModeToolPolicy` (all read tools) + `RoleAwareToolPolicy` (customers get only `CUSTOMER_TOOLS`; every MCP tool must be classified in `CUSTOMER_TOOLS` or `OPERATOR_TOOLS`, enforced by a test) |
| [runner.py](runner.py) | `MCPToolRunner` — MCP dispatch + result truncation |
| [store.py](store.py) | `ConversationStore` — tenant-scoped reads/writes against ClickHouse |
| [stream.py](stream.py) | SSE event encoder (`encode_sse`, `event_stream`) |
| [__init__.py](__init__.py) | Public re-exports |

## Public contract

Callers depend on the `AssistantService` Protocol in
[contract.py](contract.py), never on `DefaultAssistantService`.

HTTP surface is mounted at **`/cockpit/assistant/*`** by `main_api.py`
(`app/api/routes_assistant.py`) and streams over SSE. The prefix is
deliberately UI-internal; a public `/api/v1/assistant/*` is a separate
product decision, not an alias to add casually.

## Design decisions

These were locked before the service was built and the code still depends on
them — change them deliberately, not incidentally.

| Decision | Choice | Why |
|---|---|---|
| Endpoint prefix | `/cockpit/assistant/*` | UI-internal; a public API is a product decision |
| Conversation store | ClickHouse (`assistant_conversations`, `assistant_turns`) | No second database for this; reuses the existing client |
| Model selection | `ModelRegistry` — `claude-sonnet-5` default, `claude-opus-5` for `/explain` | Same code path, per-turn switch; the model id is in the cache key, so switching invalidates it |
| Thinking | **Adaptive**, always on, depth via `output_config.effort` (`medium` default / `xhigh` for `/explain`) | The fixed `budget_tokens` form and any `temperature` return a **400** on these models. Thinking also stays on because with it disabled the model can write a tool call into visible text instead of emitting a `tool_use` block — the turn succeeds and the call never runs |
| System prompt source | Versioned markdown in-repo behind `prompts.current()` (**`v2` today**) | Reviewable in PRs; its hash keys the cache, so a prompt edit invalidates it. DB-backed live editing deliberately not built |
| Write-tool allowlist | Opt-in, one tool per change | Each write tool lands with its own confirm-card copy and tests |

> Model ids are exact strings — never append a date suffix. When bumping
> them, re-check the thinking/effort contract and update `_PRICING_PER_1K`
> in [service.py](service.py) in the same change: an unknown model there
> logs an error and shows the cost footer as $0.00.

## Design rules

Per [`service_modules.md`](../../../docs/standards/service_modules.md):

- **Contract first.** Depend on the Protocol, not the class.
- **Tenant-scoped reads.** Every storage query filters by
  `principal.tenant_id`. With auth on, `routes_assistant` passes the caller's
  session principal (so a tenant only sees its own conversations); the tool
  allowlist is enforced both when building the prompt and again at execution
  time (the model can emit any tool name). Access model:
  [`docs/product/access_control.md`](../../../docs/product/access_control.md).
- **No silent failures.** Anthropic and MCP errors surface as `ERROR` events in
  the stream *and* as structured log lines.
- **Result objects over raises.** Tool dispatch returns a typed
  `ToolCall.result` / `.error`; only programmer errors raise.
- **Lazy imports for heavy deps** (`anthropic`, `clickhouse_connect`) so
  importing this package stays cheap.

## Env

Set `ANTHROPIC_API_KEY` in `.env`. The Anthropic SDK reads it directly; the
assistant does not duplicate the name in `app/config.py` (same pattern as the
trading `LLMAgent`).

## Testing

```bash
poetry run pytest app/services/assistant/tests -q
```

Unit tests mock the Anthropic client and the MCP runner. The ClickHouse-backed
store tests carry the `integration` marker and need
`docker compose --profile ch up -d`.
