import { NavLink } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { branding } from "@/branding";
import { LogoMark } from "@/components/brand/LogoMark";
import { allFlags } from "@/flags";
import { useCurrentUser } from "@/auth/useCurrentUser";
import { cn } from "@/lib/utils";
import { NAV_ITEMS, type NavCategory, type NavItem } from "./nav-items";

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  // Mobile drawer state; on desktop the sidebar is always rendered.
  mobileOpen: boolean;
  onMobileClose: () => void;
}

const CATEGORY_ORDER: readonly NavCategory[] = [
  "Overview",
  "Markets",
  "Strategies",
  "Data",
  "Agent",
  "Admin",
];

export function Sidebar({
  collapsed,
  onToggle,
  mobileOpen,
  onMobileClose,
}: SidebarProps) {
  const flags = allFlags();
  const user = useCurrentUser();
  const isOperator = user.permissions.includes("operator.access");
  const visible: NavItem[] = NAV_ITEMS.filter(
    (item) => flags[item.flag] === true && (!item.adminOnly || isOperator),
  );

  const grouped = CATEGORY_ORDER.map((cat) => ({
    category: cat,
    items: visible.filter((i) => i.category === cat),
  })).filter((g) => g.items.length > 0);

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen ? (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-30 bg-black/60 md:hidden"
          onClick={onMobileClose}
        />
      ) : null}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex h-full flex-col border-r border-border bg-bg-base/92 shadow-[18px_0_60px_rgba(0,0,0,0.28)] backdrop-blur-xl transition-[transform,width] duration-200 before:pointer-events-none before:absolute before:inset-0 before:bg-[linear-gradient(180deg,rgba(46,196,255,0.08),transparent_32%,rgba(255,255,255,0.018))]",
          // Desktop width (collapsed vs expanded).
          collapsed ? "md:w-14" : "md:w-56",
          // Mobile: slide-in drawer over content.
          "w-64",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        )}
        aria-label="Primary navigation"
      >
        <div className="relative flex h-16 items-center gap-3 border-b border-border px-3">
          <LogoMark wordmark={!collapsed} />
          {collapsed ? (
            <span className="sr-only">{branding.productName}</span>
          ) : (
            <div className="min-w-0 leading-tight">
              <div className="truncate text-[10px] uppercase tracking-wider text-fg-subtle">
                {branding.productTagline}
              </div>
            </div>
          )}
        </div>

        <nav className="relative flex-1 overflow-y-auto py-3">
          {grouped.length === 0 ? (
            <div className="px-3 text-xs text-fg-subtle">
              {collapsed ? "·" : "No pages enabled. Flip flags in src/flags.ts."}
            </div>
          ) : null}
          {grouped.map((group) => (
            <div key={group.category} className="mb-4">
              {!collapsed ? (
                <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
                  {group.category}
                </div>
              ) : null}
              <ul className="space-y-0.5 px-2">
                {group.items.map((item) => (
                  <li key={item.href}>
                    <NavLink
                      to={item.href}
                      end={item.href === "/"}
                      onClick={onMobileClose}
                      className={({ isActive }) =>
                        cn(
                          "relative flex min-h-9 items-center gap-3 rounded-md px-2 py-1.5 text-sm transition-colors",
                          isActive
                            ? "accent-rail border border-accent/20 bg-accent/10 pl-4 text-fg-base shadow-[0_0_24px_rgba(46,196,255,0.08)]"
                            : "text-fg-muted hover:bg-bg-muted/70 hover:text-fg-base",
                          collapsed && isActive && "pl-2",
                        )
                      }
                    >
                      <item.icon className="h-4 w-4 shrink-0 text-current" aria-hidden />
                      {!collapsed ? <span>{item.label}</span> : null}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        {/* Collapse toggle (desktop only) */}
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="relative hidden h-10 items-center justify-center border-t border-border text-fg-subtle hover:bg-bg-muted/70 hover:text-fg-base md:flex"
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </button>
      </aside>
    </>
  );
}
