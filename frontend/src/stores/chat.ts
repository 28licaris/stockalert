/**
 * Chat state for the AI assistant panel.
 *
 * A Zustand store (not React Query) because an SSE turn is a long-lived stream
 * that mutates one message incrementally — that doesn't fit the query/cache
 * lifecycle. The store owns the current conversation id, the message list, and
 * the streaming flag, and `send()` orchestrates the full turn:
 * start-conversation (once) → stream-turn → fold each SSE event into state.
 *
 * Conversation state is in-memory (resets on reload). Resuming a prior
 * conversation across reloads (via GET /conversations/{id}) is a future add.
 */
import { create } from "zustand";
import {
  startConversation,
  streamTurn,
  type AssistantStreamEvent,
} from "@/api/assistant";

export interface ToolActivity {
  callId: string;
  name: string;
  status: "running" | "ok" | "error";
  elapsedS?: number;
}

export interface TurnStats {
  model: string;
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
  cacheHit: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[];
  stats?: TurnStats;
  error?: string;
}

interface ChatState {
  conversationId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  send: (text: string) => Promise<void>;
  stop: () => void;
  reset: () => void;
}

// Module-level so it isn't part of rendered state. One in-flight turn at a time.
let abortController: AbortController | null = null;
let idSeq = 0;
const nextId = () => `m${++idSeq}`;

type PatchFn = (fn: (m: ChatMessage) => ChatMessage) => void;

export const useChatStore = create<ChatState>((set, get) => ({
  conversationId: null,
  messages: [],
  streaming: false,

  reset: () => {
    abortController?.abort();
    abortController = null;
    set({ conversationId: null, messages: [], streaming: false });
  },

  stop: () => {
    abortController?.abort();
    abortController = null;
    set({ streaming: false });
  },

  send: async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || get().streaming) return;

    const userMsg: ChatMessage = {
      id: nextId(),
      role: "user",
      content: trimmed,
      tools: [],
    };
    const asstMsg: ChatMessage = {
      id: nextId(),
      role: "assistant",
      content: "",
      tools: [],
    };
    set((s) => ({ messages: [...s.messages, userMsg, asstMsg], streaming: true }));

    // Patch only the in-flight assistant message.
    const patch: PatchFn = (fn) =>
      set((s) => ({
        messages: s.messages.map((m) => (m.id === asstMsg.id ? fn(m) : m)),
      }));

    try {
      let cid = get().conversationId;
      if (!cid) {
        const conv = await startConversation();
        cid = conv.id;
        set({ conversationId: cid });
      }

      abortController = new AbortController();
      for await (const ev of streamTurn(
        cid,
        { user_msg: trimmed },
        abortController.signal,
      )) {
        applyEvent(ev, patch);
      }
    } catch (err) {
      // A user-initiated abort is expected — don't surface it as an error.
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        const message = err instanceof Error ? err.message : String(err);
        patch((m) => ({ ...m, error: message }));
      }
    } finally {
      abortController = null;
      set({ streaming: false });
    }
  },
}));

interface EventPayload {
  text?: string;
  name?: string;
  call_id?: string;
  error?: string | null;
  elapsed_s?: number;
  model?: string;
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: number;
  cache_hit?: boolean;
  kind?: string;
  message?: string;
}

/** Fold one SSE event into the in-flight assistant message. */
function applyEvent(ev: AssistantStreamEvent, patch: PatchFn): void {
  const p = ev.payload as EventPayload;

  switch (ev.type) {
    case "text_delta":
      patch((m) => ({ ...m, content: m.content + (p.text ?? "") }));
      break;

    case "tool_call_started":
      patch((m) => ({
        ...m,
        tools: [
          ...m.tools,
          { callId: p.call_id ?? "", name: p.name ?? "tool", status: "running" },
        ],
      }));
      break;

    case "tool_result":
    case "tool_error":
      patch((m) => ({
        ...m,
        tools: m.tools.map((t) =>
          t.callId === p.call_id
            ? { ...t, status: p.error ? "error" : "ok", elapsedS: p.elapsed_s }
            : t,
        ),
      }));
      break;

    case "turn_completed":
      patch((m) => ({
        ...m,
        stats: {
          model: p.model ?? "",
          tokensIn: p.tokens_in ?? 0,
          tokensOut: p.tokens_out ?? 0,
          costUsd: p.cost_usd ?? 0,
          cacheHit: Boolean(p.cache_hit),
        },
      }));
      break;

    case "error":
      patch((m) => ({ ...m, error: p.message ?? p.kind ?? "Stream error" }));
      break;

    // thinking_delta / artifact_ready / quota_warning / pending_confirm / done
    // are not surfaced in this slice.
    default:
      break;
  }
}
