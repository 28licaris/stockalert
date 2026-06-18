/**
 * Client for the cockpit AI assistant (`/cockpit/assistant/*`).
 *
 * These routes are NOT in the generated openapi types (`types.gen.ts`) and the
 * turn endpoint streams Server-Sent Events, which `openapi-fetch` can't
 * consume — so this is a small hand-written client over raw `fetch`. The types
 * mirror `app/services/assistant/schemas.py`; keep them in sync if the backend
 * contract changes.
 */

const BASE = "/cockpit/assistant";

export interface Conversation {
  id: string;
  owner_id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  turn_count: number;
  total_cost_usd: number;
  deleted_at: string | null;
}

/** SSE event types emitted by the turn stream (StreamEventType in schemas.py). */
export type StreamEventType =
  | "text_delta"
  | "thinking_delta"
  | "tool_call_started"
  | "tool_call_pending_confirm"
  | "tool_result"
  | "tool_error"
  | "artifact_ready"
  | "quota_warning"
  | "turn_completed"
  | "error"
  | "done";

export interface AssistantStreamEvent {
  type: StreamEventType;
  payload: Record<string, unknown>;
}

export interface ContinueRequest {
  user_msg: string;
  model?: string | null;
  use_extended_thinking?: boolean;
  client_request_id?: string | null;
}

/** Create a new conversation and return its metadata. */
export async function startConversation(title?: string): Promise<Conversation> {
  const qs = title ? `?title=${encodeURIComponent(title)}` : "";
  const res = await fetch(`${BASE}/conversations${qs}`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`Failed to start conversation (${res.status})`);
  }
  return (await res.json()) as Conversation;
}

/**
 * Stream one assistant turn as an async generator of SSE events.
 *
 * The backend response is `text/event-stream`; we parse `data: {json}\n\n`
 * frames off the `ReadableStream` and yield each decoded event. The caller
 * drives rendering off the event `type` (see StreamEventType). Pass an
 * `AbortSignal` to cancel an in-flight turn.
 */
export async function* streamTurn(
  conversationId: string,
  body: ContinueRequest,
  signal?: AbortSignal,
): AsyncGenerator<AssistantStreamEvent> {
  const res = await fetch(`${BASE}/conversations/${conversationId}/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Assistant turn failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!dataLine) continue;
        const json = dataLine.slice(5).trim();
        if (!json) continue;
        try {
          yield JSON.parse(json) as AssistantStreamEvent;
        } catch {
          // Skip a malformed frame rather than killing the stream.
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}
