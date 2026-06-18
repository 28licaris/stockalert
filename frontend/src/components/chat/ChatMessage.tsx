import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, Check, Loader2, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage as Msg, ToolActivity } from "@/stores/chat";

/**
 * One chat row. User messages are a right-aligned bubble; assistant messages
 * render markdown, with any tool calls shown as live activity chips and a
 * muted cost/token footer once the turn completes.
 */
export function ChatMessage({ m }: { m: Msg }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-bg-muted px-3 py-2 text-sm text-fg-base">
          {m.content}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {m.tools.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {m.tools.map((t) => (
            <ToolChip key={t.callId} tool={t} />
          ))}
        </div>
      )}

      {m.content && (
        <div className="markdown text-sm leading-relaxed text-fg-base">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        </div>
      )}

      {m.error && (
        <div className="flex items-start gap-1.5 rounded-md border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{m.error}</span>
        </div>
      )}

      {m.stats && (
        <div className="text-[10px] text-fg-subtle">
          {m.stats.model} · {m.stats.tokensIn}→{m.stats.tokensOut} tok ·{" "}
          ${m.stats.costUsd.toFixed(4)}
          {m.stats.cacheHit ? " · cached" : ""}
        </div>
      )}
    </div>
  );
}

function ToolChip({ tool }: { tool: ToolActivity }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px]",
        tool.status === "running" && "border-border bg-bg-muted text-fg-muted",
        tool.status === "ok" && "border-up/40 bg-up/10 text-up",
        tool.status === "error" && "border-danger/40 bg-danger/10 text-danger",
      )}
    >
      {tool.status === "running" ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : tool.status === "ok" ? (
        <Check className="h-3 w-3" />
      ) : (
        <Wrench className="h-3 w-3" />
      )}
      {tool.name}
      {tool.elapsedS != null && tool.status !== "running" && (
        <span className="text-fg-subtle">{tool.elapsedS.toFixed(1)}s</span>
      )}
    </span>
  );
}
