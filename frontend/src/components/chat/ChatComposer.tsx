import { useRef, useState } from "react";
import { ArrowUp, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat";

/**
 * Message input. Enter sends; Shift+Enter inserts a newline. While a turn is
 * streaming the send button becomes a stop button (aborts the SSE stream).
 */
export function ChatComposer() {
  const [text, setText] = useState("");
  const streaming = useChatStore((s) => s.streaming);
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const value = text.trim();
    if (!value || streaming) return;
    setText("");
    void send(value);
    // Reset height after clearing.
    requestAnimationFrame(() => {
      if (taRef.current) taRef.current.style.height = "auto";
    });
  };

  return (
    <div className="shrink-0 border-t border-border p-3">
      <div className="flex items-end gap-2 rounded-md border border-border bg-bg-base px-2 py-1.5 focus-within:border-accent">
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Ask about a symbol, signal, the lake…"
          className="max-h-40 min-h-[1.5rem] flex-1 resize-none bg-transparent text-sm text-fg-base placeholder:text-fg-subtle focus:outline-none"
        />
        <button
          type="button"
          onClick={streaming ? stop : submit}
          disabled={!streaming && !text.trim()}
          aria-label={streaming ? "Stop" : "Send"}
          className={cn(
            "grid h-7 w-7 shrink-0 place-items-center rounded-md transition-colors",
            streaming
              ? "bg-bg-muted text-fg-base hover:bg-bg-elevated"
              : "bg-accent text-accent-fg disabled:bg-bg-muted disabled:text-fg-subtle",
          )}
        >
          {streaming ? (
            <Square className="h-3.5 w-3.5" />
          ) : (
            <ArrowUp className="h-4 w-4" />
          )}
        </button>
      </div>
      <p className="mt-1.5 px-1 text-[10px] text-fg-subtle">
        Read-only tools · responses can be wrong — verify before acting.
      </p>
    </div>
  );
}
