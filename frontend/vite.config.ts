import { defineConfig } from "vitest/config";

export default defineConfig({
  build: { outDir: "dist", sourcemap: true },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
    coverage: { reportsDirectory: "coverage" },
  },
});
