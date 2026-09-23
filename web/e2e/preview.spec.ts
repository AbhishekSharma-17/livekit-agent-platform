import { expect, test } from "@playwright/test";

/**
 * Preview route smoke test (docs/UI_UX_SPEC.md §7.11 item 4, optional).
 *
 * Walks every combination `scene=list` advertises and asserts the page
 * rendered without a console error — the same source of truth
 * `scripts/ui-capture.mjs` uses, so a new scene is covered here with no
 * change to this file.
 */
test("every preview scene combination renders without a console error", async ({ page }) => {
  // `scene=list` enumerates every combination across every scene (~70 page
  // loads including both viewports' worth of surfaces) — comfortably over
  // Playwright's default 30 s per-test timeout regardless of dev-server load.
  test.setTimeout(180_000);

  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/console/preview/panels?scene=list");
  const raw = await page.locator('[data-testid="scene-list"]').textContent();
  const entries = JSON.parse(raw ?? "[]") as { scene: string; query: string; surfaces: string[] }[];
  expect(entries.length).toBeGreaterThan(0);

  for (const entry of entries) {
    for (const surface of entry.surfaces) {
      errors.length = 0;
      await page.goto(`/console/preview/panels?${entry.query}&surface=${surface}`);
      await expect(page.locator('[data-testid="preview-shell"]')).toBeVisible();
      expect(errors, `console errors on scene=${entry.scene} (${entry.query}, surface=${surface})`).toEqual([]);
    }
  }
});
