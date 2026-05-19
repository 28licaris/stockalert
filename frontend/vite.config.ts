import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND_URL =
  process.env.STOCKALERT_BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },

  server: {
    // Bind to all interfaces so a phone / tablet / other-LAN device
    // can reach the dev server at http://<host-LAN-IP>:5173/app/.
    // The Vite-proxied /api + /ws requests still flow to FastAPI on
    // 127.0.0.1:8000 because the proxy runs on the host, not the
    // phone. Safe behind a home router; never expose the dev server
    // to the public internet.
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND_URL, changeOrigin: true },
      "/mcp": { target: BACKEND_URL, changeOrigin: true },
      "/openapi.json": { target: BACKEND_URL, changeOrigin: true },
      "/ws": {
        target: BACKEND_URL.replace(/^http/, "ws"),
        ws: true,
        changeOrigin: true,
      },
    },
  },

  // FastAPI mounts this at /app — see app/main_api.py.
  // Same-origin in production, so /api and /ws work without a proxy.
  base: "/app/",
  build: {
    outDir: "../app/static/dist",
    emptyOutDir: true,
    sourcemap: true,
    target: "es2022",
  },
});
