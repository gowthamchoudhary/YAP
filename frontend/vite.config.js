import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: /api and /ws go to the FastAPI backend (default localhost:8000).
// Any request that bypasses the proxy (absolute URL) will hit CORS on the backend,
// which allows localhost:5173 by default.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
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
