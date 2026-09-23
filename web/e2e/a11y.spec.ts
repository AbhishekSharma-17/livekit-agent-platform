import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Accessibility floor (V2-19C, WP-12): no critical or serious axe finding on
 * the console's static routes, the sign-in card, the session's unavailable
 * page and the stage in its `reconnecting` state (R-V2-19 — the caption must
 * keep full contrast while the media dims).
 *
 * Read-only: every route is a plain navigation; nothing is clicked. Routes
 * that need live ids (an agent, a session) are covered by
 * `scripts/ui-capture.mjs`, which discovers them from the dev api.
 */
const ROUTES = [
  "/",
  "/login",
  "/console",
  "/console/agents",
  "/console/agents/new",
  "/console/knowledge",
  "/console/tools",
  "/console/connections",
  "/console/providers",
  "/console/sessions",
  "/console/analytics",
  "/console/settings",
  "/s/does-not-exist-zzz",
  "/console/preview/panels?scene=session&layout=side&state=reconnecting&test=0&embed=0&surface=dark",
  "/console/preview/panels?scene=session&layout=wide&state=reconnecting&test=0&embed=0&surface=dark",
  "/console/preview/panels?scene=session&layout=side&state=listening&test=0&embed=1&surface=dark",
];

for (const route of ROUTES) {
  test(`no critical or serious axe findings on ${route}`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto(route, { waitUntil: "networkidle", timeout: 90_000 });
    // A redirect (e.g. `/console/*` → `/login` with the admin bypass off)
    // must fail here, not pass as "the sign-in card is accessible".
    expect(new URL(page.url()).pathname).toBe(new URL(route, "http://x").pathname);
    await page.locator("h1").first().waitFor({ timeout: 30_000 });
    const result = await new AxeBuilder({ page }).analyze();
    const blocking = result.violations
      .filter((violation) => violation.impact === "critical" || violation.impact === "serious")
      .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`);
    expect(blocking, `axe on ${route}`).toEqual([]);
  });
}
