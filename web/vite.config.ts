import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
// T36: vitest 4 — the `/// <reference types="vitest" />` triple-slash was
// removed; importing defineConfig from vitest/config gives the merged
// vite+vitest types and still works when plain `vite` runs this file.
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "node",
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      // 保守门槛：守护既有覆盖不下滑。当前整体 Lines ~33%（pages 多为 0%、
      // utils 96%），真正提升靠为 page 组件补测试，而非抬高硬门槛。
      // T36: vitest 4 的 v8 provider 默认 AST-aware remapping，函数/分支
      // 统计口径更严格（functions 26.6%、branches 37.1%）， thresholds 按
      // 新口径一次性校准，此后仍守护不下滑。
      thresholds: {
        lines: 30,
        statements: 30,
        functions: 25,
        branches: 35,
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
