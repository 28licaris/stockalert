# AI Assistant panel

A collapsible right-side chat panel for talking to the cockpit's AI assistant —
ask questions about market data, signals, coverage, and run read-only analysis.
The assistant can call the server's tools (live bars, signals, screener,
indicators, backtests, …) to answer.

This is **frontend only**. It consumes the existing backend assistant API; there
is no new server code. See `app/services/assistant/` and
`app/api/routes_assistant.py`.

## Requirements

The backend needs `ANTHROPIC_API_KEY` set (see `.env.example`). Without it, a
turn streams an `error` event and the panel shows it inline — the UI still works,
it just can't get a model response. With the key set, responses stream live.

## How it works

```
Topbar ✨ button / ⌘·Ctrl+I        AppShell (owns `ui.chat.open`, persisted)
        └────────── toggles ───────────┘
                                        └── <ChatPanel> (3rd column, width-animated)
                                              ├── <ChatMessage>  (markdown + tool chips + cost footer)
                                              └── <ChatComposer> (textarea; Enter=send, Shift+Enter=newline)

useChatStore.send(text)
  → POST /cockpit/assistant/conversations            (once, lazily → conversationId)
  → POST /cockpit/assistant/conversations/{id}/turn  (SSE)
  → for each event: fold into the in-flight assistant message
```

### Files

| File | Responsibility |
|---|---|
| `src/api/assistant.ts` | Typed client over raw `fetch`. `startConversation()` + `streamTurn()` (an async generator that parses `text/event-stream` frames). Types mirror `app/services/assistant/schemas.py`. |
| `src/stores/chat.ts` | Zustand store. Owns `conversationId`, `messages`, `streaming`. `send()` orchestrates the turn; `applyEvent()` folds each SSE event into state. `stop()` aborts; `reset()` starts a new chat. |
| `src/components/chat/ChatPanel.tsx` | Panel shell: header (new chat / close), message list with autoscroll, empty-state suggestions, composer. |
| `src/components/chat/ChatMessage.tsx` | One row. User = bubble; assistant = markdown (`react-markdown` + `remark-gfm`), tool-activity chips, and a muted `model · tokens · $cost` footer. |
| `src/components/chat/ChatComposer.tsx` | Auto-growing input; send/stop button. |
| `AppShell.tsx` / `Topbar.tsx` | Mount + toggle. |

### Why not React Query / openapi-fetch?

A turn is a long-lived SSE stream that mutates **one** message incrementally —
that doesn't fit the query/cache lifecycle, and `openapi-fetch` can't stream. So
the panel uses a Zustand store + a hand-written `fetch`/`ReadableStream` client.
The `/cockpit/assistant/*` routes also aren't in the generated openapi types, so
the request/response types are hand-written here (keep them in sync with
`schemas.py`).

## SSE event contract

`streamTurn()` yields `{ type, payload }`. Handled types (`StreamEventType`):

- `text_delta` → append `payload.text` to the assistant message.
- `tool_call_started` → add a running chip (`payload.name`, `payload.call_id`).
- `tool_result` / `tool_error` → resolve the chip to ok/error.
- `turn_completed` → set the cost/token footer.
- `error` → show the error inline.
- `done` → terminal (stream ends).

Not yet surfaced (room to grow): `thinking_delta`, `tool_call_pending_confirm`,
`artifact_ready`, `quota_warning`.

## Extending

- **Resume across reloads** — persist `conversationId` and hydrate via
  `GET /cockpit/assistant/conversations/{id}` on mount (conversation history is
  already stored server-side in `assistant_turns`).
- **Conversation list / switcher** — `GET /cockpit/assistant/conversations`.
- **Write-tool confirmation** — handle `tool_call_pending_confirm` and
  `POST …/confirm` (backend lands this in AS-2; currently returns 501).
- **Extended thinking / model picker** — `ContinueRequest` already accepts
  `use_extended_thinking` and `model`; wire a control in the composer.
- **Mobile** — the panel is `md+` only today; add a full-screen drawer for small
  screens.
