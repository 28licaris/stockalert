import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, LogOut, Settings, ShieldCheck } from "lucide-react";
import { useAuth } from "@/auth/auth-context";
import { useCurrentUser } from "@/auth/useCurrentUser";
import { cn } from "@/lib/utils";

export function UserMenu() {
  const user = useCurrentUser();
  const { signOut, signingOut, error } = useAuth();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const initials = user.displayName
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    window.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", escape);
    };
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        data-testid="user-menu-trigger"
        aria-label="Open account menu"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="group flex items-center gap-2 rounded-xl border border-transparent py-1 pl-1 pr-2 transition hover:border-border hover:bg-bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-accent to-violet-500 text-[11px] font-semibold text-white shadow-lg shadow-accent/10">
          {initials || "U"}
        </span>
        <span className="hidden min-w-0 text-left leading-tight lg:block">
          <span className="block max-w-32 truncate text-xs font-medium text-fg-base">
            {user.displayName}
          </span>
          <span className="block text-[10px] capitalize text-fg-subtle">
            {user.roles[0]}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "hidden h-3.5 w-3.5 text-fg-subtle transition-transform lg:block",
            open && "rotate-180",
          )}
        />
      </button>

      {open ? (
        <div
          role="menu"
          data-testid="user-menu"
          className="absolute right-0 top-[calc(100%+0.6rem)] z-50 w-72 overflow-hidden rounded-2xl border border-border bg-bg-elevated/95 p-2 shadow-2xl shadow-black/40 backdrop-blur-xl"
        >
          <div className="rounded-xl bg-bg-muted/70 p-3">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/15 text-xs font-semibold text-accent">
                {initials || "U"}
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-fg-base">
                  {user.displayName}
                </p>
                <p className="truncate text-xs text-fg-subtle">
                  {user.email ?? "Verified account"}
                </p>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-2 border-t border-border/70 pt-3 text-[11px] text-fg-muted">
              <ShieldCheck className="h-3.5 w-3.5 text-success" />
              Protected session
              <span className="ml-auto rounded-full bg-success/10 px-2 py-0.5 font-medium text-success">
                Active
              </span>
            </div>
          </div>

          {error ? (
            <p className="px-3 py-2 text-xs leading-5 text-danger">{error}</p>
          ) : null}

          <Link
            to="/settings"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="mt-1 flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm text-fg-muted transition hover:bg-bg-muted hover:text-fg-base"
          >
            <Settings className="h-4 w-4" />
            Security settings
          </Link>

          <button
            type="button"
            role="menuitem"
            disabled={signingOut}
            onClick={() => void signOut().catch(() => undefined)}
            className="mt-1 flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm text-fg-muted transition hover:bg-danger/10 hover:text-danger disabled:opacity-60"
          >
            <LogOut className="h-4 w-4" />
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
