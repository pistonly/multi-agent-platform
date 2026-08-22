/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "node",
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      // 保守门槛：守护既有覆盖不下滑。当前整体 Lines ~33%（pages 多为 0%、
      // utils 96%），真正提升靠为 page 组件补测试，而非抬高硬门槛。
      thresholds: {
        lines: 30,
        statements: 30,
        functions: 30,
        branches: 55,
      },
    },
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
