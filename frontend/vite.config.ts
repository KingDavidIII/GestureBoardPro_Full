import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    proxy: {
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
    coverage: {
      reportsDirectory: "coverage",
    },
  },
});
