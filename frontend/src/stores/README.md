# stores/

Zustand stores for client state that does **not** fit the query/cache lifecycle.

| File | Owns |
|---|---|
| [chat.ts](chat.ts) | Assistant panel: conversation id, message list, streaming flag, `send()` |

## When a store is the right answer

Almost never. **Server state belongs to TanStack Query**
([`../api/`](../api/README.md)); per-viewer UI preferences belong in
`localStorage` via [`../lib/storage.ts`](../lib/storage.ts).

A store is justified when neither fits — `chat.ts` exists because an SSE turn
is a long-lived stream that mutates **one message incrementally**, which the
query/cache model does not express. `send()` orchestrates the whole turn:
start-conversation (once) → stream-turn → fold each SSE event into state.

Conversation state is in-memory and resets on reload; resuming a prior
conversation via `GET /conversations/{id}` is deliberately not built yet.

Adding a store for ordinary server data is a design choice that needs a
reason — see
[`../../../docs/standards/engagement.md`](../../../docs/standards/engagement.md).
