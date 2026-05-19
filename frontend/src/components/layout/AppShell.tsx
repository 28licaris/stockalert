import { useState } from "react";
import { Outlet } from "react-router-dom";
import { useUserSetting } from "@/lib/storage";
import { cn } from "@/lib/utils";
import { MarketBanner } from "@/components/market/MarketBanner";
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

  return (
    <div className="flex h-full min-h-0 flex-col">
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
          <Topbar onMobileMenuOpen={() => setMobileOpen(true)} />
          <main className="min-h-0 flex-1 overflow-auto bg-bg-base">
            <Outlet />
          </main>
        </div>
      </div>
      <StatusBar />
    </div>
  );
}
