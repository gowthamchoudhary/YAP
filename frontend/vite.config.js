import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: /api and /ws go to the FastAPI backend (default localhost:8000).
// Any request that bypasses the proxy (absolute URL) will hit CORS on the backend,
// which allows localhost:5173 by default.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    // Managed Freebuff preview serves the dev server through the
    // *.e2b.app tunnel host, which Vite blocks by default (CVE-2025-30208-style
    // host-header protection). Allow the preview host explicitly.
    allowedHosts: ["5173-ivyjzh3od97cccdlb7opb.e2b.app"],
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: process.env.BACKEND_URL || "http://localhost:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
