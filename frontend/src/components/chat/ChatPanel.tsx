import { useEffect, useRef } from "react";
import { Plus, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat";
import { ChatMessage } from "./ChatMessage";
import { ChatComposer } from "./ChatComposer";

const SUGGESTIONS = [
  "What's NVDA's coverage in ClickHouse?",
  "Show me the latest signals for AAPL",
  "Which symbols are the biggest movers today?",
];

/**
 * The AI assistant side panel. Talks to the existing `/cockpit/assistant/*`
 * backend (SSE turns + a tool-calling agent over the MCP tools). State lives in
 * `useChatStore`; this component is presentation + autoscroll only.
 */
export function ChatPanel({ onClose }: { onClose: () => void }) {
  const messages = useChatStore((s) => s.messages);
  const reset = useChatStore((s) => s.reset);
  const send = useChatStore((s) => s.send);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Autoscroll to the newest content as the turn streams in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div className="flex h-full flex-col bg-bg-subtle">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3">
        <span className="flex items-center gap-2 text-sm font-semibold text-fg-base">
          <Sparkles className="h-4 w-4 text-accent" />
          Assistant
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={reset}
            aria-label="New chat"
            title="New chat"
          >
            <Plus className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close assistant"
            title="Close (⌘/Ctrl+I)"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-3 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col justify-center gap-3 text-center">
            <p className="text-sm text-fg-muted">
              Ask about your market data, signals, coverage, or run analysis —
              the assistant can query the server's tools for you.
            </p>
            <div className="space-y-1.5">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="w-full rounded-md border border-border bg-bg-base px-3 py-2 text-left text-xs text-fg-muted hover:bg-bg-muted hover:text-fg-base"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((m) => (
              <ChatMessage key={m.id} m={m} />
            ))}
          </div>
        )}
      </div>

      <ChatComposer />
    </div>
  );
}
