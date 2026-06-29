import { useState } from "react";
import { Bot, Menu } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { SymbolSearchInput } from "@/components/symbol/SymbolSearchInput";
import { cn } from "@/lib/utils";
import { UserMenu } from "./UserMenu";

interface TopbarProps {
  onMobileMenuOpen: () => void;
  chatOpen: boolean;
  onToggleChat: () => void;
}

export function Topbar({
  onMobileMenuOpen,
  chatOpen,
  onToggleChat,
}: TopbarProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  const handleSubmit = (raw: string) => {
    const norm = raw.trim().toUpperCase();
    if (!norm) return;
    navigate(`/symbol/${encodeURIComponent(norm)}`);
  };

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-bg-base/80 px-3 shadow-[0_12px_34px_rgba(0,0,0,0.16)] backdrop-blur-xl md:px-4">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="md:hidden"
        onClick={onMobileMenuOpen}
        aria-label="Open navigation"
      >
        <Menu className="h-5 w-5" />
      </Button>

      <SymbolSearchInput
        value={query}
        onChange={setQuery}
        onSubmit={handleSubmit}
        placeholder="Search ticker or company"
        clearOnSubmit
        className="max-w-lg flex-1"
      />

      <Button
        type="button"
        variant={chatOpen ? "default" : "outline"}
        size="sm"
        onClick={onToggleChat}
        aria-label="Toggle AI assistant"
        aria-pressed={chatOpen}
        title="AI assistant (⌘/Ctrl+I)"
        className={cn("ml-auto hidden sm:inline-flex", chatOpen && "shadow-[0_0_36px_rgba(46,196,255,0.24)]")}
      >
        <Bot className="h-4 w-4" />
        Assistant
      </Button>

      <UserMenu />
    </header>
  );
}
