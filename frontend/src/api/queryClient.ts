import { QueryClient } from "@tanstack/react-query";

/**
 * Single shared QueryClient. Tunings:
 *  - staleTime 30s: most cockpit queries (status, watchlist, etc.) are
 *    fresh-for-half-a-minute; longer means stale UI, shorter means
 *    network thrash. WebSocket pushes invalidate caches explicitly
 *    for anything that should be more real-time.
 *  - gcTime 5min: keep recently-unmounted data in memory so route
 *    transitions don't refetch on every back-button.
 *  - retry once: backend errors are usually persistent; don't hammer.
 *  - refetchOnWindowFocus off in dev: HMR + auto-refetch interact
 *    badly. The Status page will subscribe to its own poll/WS instead.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});
