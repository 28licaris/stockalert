import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { StatusPage } from "./status";
import { RecentBarsPage, SymbolPage } from "./symbol";
import { EwtPage } from "./ewt";
import { EwtGuidePage } from "./ewt-guide";
import { WatchlistsPage } from "./watchlists";
import { StreamPage } from "./stream";
import { OptionsPage } from "./options";
import { ClickHousePage } from "./clickhouse";
import { CalendarPage } from "./calendar";
import { NewsPage } from "./news";
import { EconomicPage } from "./economic";
import { SectorsPage } from "./sectors";
import { BacktestPage } from "./backtest";
import { NotFoundPage } from "./not-found";
import { LoginPage } from "./login";
import { SettingsPage } from "./settings";
import { AuthGuard } from "@/auth/AuthGuard";

/**
 * Single source of truth for routing. As new pages land they get
 * lazy-imported here. Lazy-loaded pages produce per-route chunks
 * automatically — keeps the initial bundle small.
 *
 * basename matches Vite's `base: "/app/"` in production; in dev the
 * router resolves cleanly because Vite serves from `/app/` too.
 */
export const router = createBrowserRouter(
  [
    { path: "/login", element: <LoginPage /> },
    {
      element: <AuthGuard />,
      children: [
        {
          path: "/",
          element: <AppShell />,
          children: [
            { index: true, element: <StatusPage /> },
            { path: "charts", element: <SymbolPage /> },
            { path: "charts/:ticker", element: <SymbolPage /> },
            { path: "charts/:ticker/bars", element: <RecentBarsPage /> },
            { path: "symbol", element: <Navigate to="/charts" replace /> },
            { path: "symbol/:ticker", element: <SymbolPage /> },
            { path: "ewt", element: <EwtPage /> },
            { path: "ewt/guide", element: <EwtGuidePage /> },
            { path: "ewt/:ticker", element: <EwtPage /> },
            { path: "watchlists", element: <WatchlistsPage /> },
            { path: "stream", element: <StreamPage /> },
            { path: "options", element: <OptionsPage /> },
            { path: "seed", element: <Navigate to="/stream" replace /> },
            { path: "clickhouse", element: <ClickHousePage /> },
            { path: "calendar", element: <CalendarPage /> },
            { path: "news", element: <NewsPage /> },
            { path: "economic", element: <EconomicPage /> },
            { path: "sectors", element: <SectorsPage /> },
            { path: "backtest", element: <BacktestPage /> },
            { path: "runs", element: <BacktestPage /> },
            { path: "settings", element: <SettingsPage /> },
            { path: "*", element: <NotFoundPage /> },
          ],
        },
      ],
    },
  ],
  { basename: "/app" },
);
