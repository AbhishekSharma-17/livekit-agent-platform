import { defineConfig, devices } from "@playwright/test";

/**
 * E2E tests (`web/e2e/**`), against a running `pnpm dev`/`next start` —
 * see `e2e/README.md` for what each spec covers and needs.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
