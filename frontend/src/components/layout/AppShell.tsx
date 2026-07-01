import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { useUserSetting } from "@/lib/storage";
import { useCurrentUser } from "@/auth/useCurrentUser";
import { cn } from "@/lib/utils";
import { MarketBanner } from "@/components/market/MarketBanner";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { StatusBar } from "./StatusBar";

/**
 * The chrome around every cockpit page. Persistent regions:
 *   - Sidebar      (collapsible on desktop; slide-in drawer on mobile)
 *   - MarketBanner (always-visible index/futures tape; md+ only)
 *   - Topbar       (search, user)
 *   - StatusBar    (subsystem health pills at the bottom)
 * Page content renders inside <Outlet/>.
 */
export function AppShell() {
  const [collapsed, setCollapsed] = useUserSetting<boolean>(
    "ui.sidebar.collapsed",
    false,
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [chatOpen, setChatOpen] = useUserSetting<boolean>("ui.chat.open", false);
  const user = useCurrentUser();
  const isOperator = user.permissions.includes("operator.access");

  // ⌘/Ctrl+I toggles the assistant panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "i") {
        e.preventDefault();
        setChatOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setChatOpen]);

  return (
    <div className="app-grid flex h-full min-h-0 flex-col bg-bg-base">
      <div className="flex min-h-0 flex-1">
        <Sidebar
          collapsed={collapsed}
          onToggle={() => setCollapsed((c) => !c)}
          mobileOpen={mobileOpen}
          onMobileClose={() => setMobileOpen(false)}
        />

        <div
          className={cn(
            "flex min-w-0 flex-1 flex-col transition-[margin] duration-200",
            // On desktop, reserve space for the sidebar. On mobile the
            // sidebar overlays content, so no margin.
            collapsed ? "md:ml-14" : "md:ml-56",
          )}
        >
          <MarketBanner />
          <Topbar
            onMobileMenuOpen={() => setMobileOpen(true)}
            chatOpen={chatOpen}
            onToggleChat={() => setChatOpen((o) => !o)}
          />
          <main className="min-h-0 flex-1 overflow-auto bg-transparent">
            <Outlet />
          </main>
        </div>

        {/* Collapsible AI assistant. Width-animated so the main content
            reflows smoothly; hidden on mobile for now. */}
        <aside
          className={cn(
            "hidden shrink-0 overflow-hidden border-l border-border bg-bg-base/80 shadow-[-24px_0_60px_rgba(0,0,0,0.22)] backdrop-blur-xl transition-[width] duration-200 md:block",
            chatOpen ? "md:w-[380px]" : "md:w-0",
          )}
          aria-hidden={!chatOpen}
        >
          <div className="h-full w-[380px]">
            <ChatPanel onClose={() => setChatOpen(false)} />
          </div>
        </aside>
      </div>
      {/* Subsystem-health strip is operator-only, and offset by the sidebar
          width so the fixed sidebar doesn't cover the provider pills. */}
      {isOperator ? (
        <div
          className={cn(
            "transition-[margin] duration-200",
            collapsed ? "md:ml-14" : "md:ml-56",
          )}
        >
          <StatusBar />
        </div>
      ) : null}
    </div>
  );
}
