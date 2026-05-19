import {
  Activity,
  BarChart3,
  Beaker,
  CandlestickChart,
  Database,
  FileBarChart,
  Gauge,
  LineChart,
  ListChecks,
  NotebookPen,
  Radio,
  Settings,
  Sparkles,
  Wrench,
  type LucideIcon,
} from "lucide-react";

/**
 * Sidebar navigation source. Each entry maps to:
 *  - a route path (`href`)
 *  - a feature flag (`flag`) that controls visibility
 *  - a category for grouping in the sidebar
 *
 * Adding a new page = add one row here + create the route file. The
 * sidebar regenerates automatically. Flags default-off for unbuilt
 * pages so the sidebar reflects what's actually shippable.
 */

export type NavCategory = "Overview" | "Markets" | "Strategies" | "Data" | "Agent" | "Admin";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  flag: string;
  category: NavCategory;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Status",      href: "/",            icon: Gauge,            flag: "page.status",      category: "Overview" },

  { label: "Symbol",      href: "/symbol",      icon: CandlestickChart, flag: "page.symbol",      category: "Markets" },
  { label: "Watchlists",  href: "/watchlists",  icon: ListChecks,       flag: "page.watchlists",  category: "Markets" },
  { label: "Stream",      href: "/stream",      icon: Radio,            flag: "page.seed",        category: "Markets" },
  { label: "Monitors",    href: "/monitors",    icon: Activity,         flag: "page.monitors",    category: "Markets" },

  { label: "Screener",    href: "/screener",    icon: Sparkles,         flag: "page.screener",    category: "Strategies" },
  { label: "Backtest",    href: "/backtest",    icon: Beaker,           flag: "page.backtest",    category: "Strategies" },
  { label: "Runs",        href: "/runs",        icon: FileBarChart,     flag: "page.runs",        category: "Strategies" },
  { label: "Journal",     href: "/journal",     icon: NotebookPen,      flag: "page.journal",     category: "Strategies" },

  { label: "Indicators",  href: "/indicators",  icon: LineChart,        flag: "page.indicators",  category: "Data" },
  { label: "Lake",        href: "/lake",        icon: Database,         flag: "page.lake",        category: "Data" },
  { label: "Coverage",    href: "/coverage",    icon: BarChart3,        flag: "page.coverage",    category: "Data" },
  { label: "ClickHouse",  href: "/clickhouse",  icon: Database,         flag: "page.clickhouse",  category: "Data" },

  { label: "MCP",         href: "/mcp",         icon: Wrench,           flag: "page.mcp",         category: "Agent" },

  { label: "Settings",    href: "/settings",    icon: Settings,         flag: "page.settings",    category: "Admin" },
] as const;
