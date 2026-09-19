import { defineConfig, devices } from "@playwright/test";

/**
 * E2E smoke tests (`web/e2e/**`), against a running `pnpm dev`/`next start`.
 * No spec files exist yet — Wave 3 (W3-E2E-INSURANCE) adds the console →
 * session → transcript smoke test.
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
