import { defineConfig } from "vite";

// The backend serves the built app from frontend/dist. During development the
// Vite dev server proxies /api and /ws to the Python backend on :8080.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
