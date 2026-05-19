import { Menu, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/auth/useCurrentUser";

interface TopbarProps {
  onMobileMenuOpen: () => void;
}

export function Topbar({ onMobileMenuOpen }: TopbarProps) {
  const user = useCurrentUser();

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-bg-base px-3 md:px-4">
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

      {/* Search trigger — non-functional placeholder. Will open the
          command palette in FE-9. */}
      <button
        type="button"
        className="flex h-9 max-w-md flex-1 items-center gap-2 rounded-md border border-border bg-bg-subtle px-3 text-left text-sm text-fg-subtle hover:bg-bg-muted"
        aria-label="Search (coming in FE-9)"
      >
        <Search className="h-4 w-4" />
        <span className="truncate">Search</span>
        <kbd className="ml-auto hidden rounded border border-border bg-bg-base px-1.5 py-0.5 font-mono text-[10px] text-fg-subtle sm:inline">
          ⌘K
        </kbd>
      </button>

      <div className="ml-auto flex items-center gap-3 text-sm">
        <div className="hidden text-right leading-tight md:block">
          <div className="text-fg-base">{user.displayName}</div>
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
            {user.plan} · {user.roles[0]}
          </div>
        </div>
        <div className="grid h-8 w-8 place-items-center rounded-full bg-bg-muted text-xs font-medium text-fg-base">
          {user.displayName.charAt(0).toUpperCase()}
        </div>
      </div>
    </header>
  );
}
