import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { StatusPage } from "./status";
import { SymbolPage } from "./symbol";
import { WatchlistsPage } from "./watchlists";
import { SeedPage } from "./seed";
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
        { path: "watchlists", element: <WatchlistsPage /> },
        { path: "seed", element: <SeedPage /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/app" },
);
