import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { StatusPage } from "./status";
import { SymbolPage } from "./symbol";
import { EwtPage } from "./ewt";
import { WatchlistsPage } from "./watchlists";
import { StreamPage } from "./stream";
import { ClickHousePage } from "./clickhouse";
import { NotFoundPage } from "./not-found";

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
    {
      path: "/",
      element: <AppShell />,
      children: [
        { index: true, element: <StatusPage /> },
        { path: "symbol", element: <SymbolPage /> },
        { path: "symbol/:ticker", element: <SymbolPage /> },
        { path: "ewt", element: <EwtPage /> },
        { path: "ewt/:ticker", element: <EwtPage /> },
        { path: "watchlists", element: <WatchlistsPage /> },
        { path: "stream", element: <StreamPage /> },
        // Back-compat alias: the old /app/seed URL now redirects to /app/stream.
        // The page was renamed in FE-CONTRACTS-4 finalisation.
        { path: "seed", element: <Navigate to="/stream" replace /> },
        { path: "clickhouse", element: <ClickHousePage /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/app" },
);
