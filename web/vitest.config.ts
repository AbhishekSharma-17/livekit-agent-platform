import path from "node:path";

import { defineConfig } from "vitest/config";

/**
 * Unit/component tests (`web/tests/**`). `passWithNoTests` is required
 * because W0-SCAFFOLD ships this config with no test files yet — Wave 1
 * (reducer, registry, generic panel) and Wave 2 (insurance notebook) add
 * them.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  // Without this, esbuild falls back to the classic JSX runtime, which
  // needs `React` as an in-scope value in every .tsx file — several vendored
  // components (e.g. `components/ui/skeleton.tsx`) only reference `React` as
  // a type and never import it as a value, so they throw `React is not
  // defined` at test runtime even though `tsc` (which reads tsconfig's own
  // `jsx: "react-jsx"`) is perfectly happy. The automatic runtime matches
  // what Next.js already does for the app itself.
  esbuild: {
    jsx: "automatic",
  },
  test: {
    // Dialog-heavy tests exceed vitest's 5 s default on a loaded machine;
    // a generous ceiling keeps the gate honest without masking hangs.
    testTimeout: 20_000,
    environment: "jsdom",
    passWithNoTests: true,
    globals: true,
    include: [
      "tests/**/*.{test,spec}.{ts,tsx}",
      "src/**/*.{test,spec}.{ts,tsx}",
    ],
  },
});
