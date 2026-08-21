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
                // Native/dev on this machine maps MAP API to :8001 (host :8000 is taken).
                // Override with VITE_API_PROXY if needed; leave VITE_API_URL empty so
                // the browser talks same-origin /api and skips CORS.
                target: process.env.VITE_API_PROXY || "http://localhost:8001",
                changeOrigin: true,
            },
        },
    },
});
