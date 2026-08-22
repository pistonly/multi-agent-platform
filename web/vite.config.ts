/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "node",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // Default MAP API port is 18400 (uncommon; 8000/8001 conflict-prone).
        // Override with VITE_API_PROXY if needed; leave VITE_API_URL empty so
        // the browser talks same-origin /api and skips CORS.
        target: process.env.VITE_API_PROXY || "http://localhost:18400",
        changeOrigin: true,
      },
    },
  },
});
