import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Each API suite starts Python processes; avoid Windows process-start contention.
    ...(process.platform === "win32" ? { fileParallelism: false, testTimeout: 30_000 } : {}),
  },
});
