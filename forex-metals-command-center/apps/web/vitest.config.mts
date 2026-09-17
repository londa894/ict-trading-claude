import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Mirror the tsconfig "@/*" path alias used by the app.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // jsdom's cold import is slow on synced folders (e.g. OneDrive, ~14 s measured); running test files
    // sequentially keeps the jsdom worker from missing Vitest's worker-start deadline under contention.
    fileParallelism: false,
  },
});
