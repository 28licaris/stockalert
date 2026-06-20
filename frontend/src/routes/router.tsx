import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { StatusPage } from "./status";
import { SymbolPage } from "./symbol";
import { WatchlistsPage } from "./watchlists";
import { StreamPage } from "./stream";
import { ClickHousePage } from "./clickhouse";
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
            { path: "symbol", element: <SymbolPage /> },
            { path: "symbol/:ticker", element: <SymbolPage /> },
            { path: "watchlists", element: <WatchlistsPage /> },
            { path: "stream", element: <StreamPage /> },
            { path: "seed", element: <Navigate to="/stream" replace /> },
            { path: "clickhouse", element: <ClickHousePage /> },
            { path: "settings", element: <SettingsPage /> },
            { path: "*", element: <NotFoundPage /> },
          ],
        },
      ],
    },
  ],
  { basename: "/app" },
);
