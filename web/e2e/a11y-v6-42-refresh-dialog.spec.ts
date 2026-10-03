import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Accessibility floor for the App action editor after Refresh schema reports a retired
 * action (V6-42, "Composio has retired this action. Pick a replacement"). Same shape as
 * `a11y-v6-19-dialogs.spec.ts` (no critical or serious axe finding, against a running dev
 * server with the admin bypass on).
 *
 * It needs one connected app action on `/console/tools` to open. Nothing seeds that here, so
 * the test skips unless `LKAP_E2E_PROVIDER_TOOL` is set (any value) on a workspace that has one.
 * The refresh answer itself is stubbed in the browser, since no live action can be made to
 * report as retired on demand.
 */
const HAS_PROVIDER_TOOL = Boolean(process.env.LKAP_E2E_PROVIDER_TOOL);

function blockingViolations(result: Awaited<ReturnType<AxeBuilder["analyze"]>>) {
  return result.violations
    .filter((violation) => violation.impact === "critical" || violation.impact === "serious")
    .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`);
}

test("no critical or serious axe findings in the App action editor with a retired action", async ({ page }) => {
  test.skip(!HAS_PROVIDER_TOOL, "set LKAP_E2E_PROVIDER_TOOL on a workspace with a connected app action to run this");
  await page.route("**/refresh-schema*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        tool_id: "stub",
        tool_slug: "STUB_ACTION",
        changed: false,
        applied: false,
        schema_version_before: null,
        schema_version_after: null,
        added: [],
        removed: [],
        modified: [],
        required_before: [],
        required_after: [],
        deprecated: true,
      }),
    }),
  );
  await page.goto("/console/tools", { waitUntil: "networkidle" });

  // Open the Edit dialog of the first row that is an app action.
  const editButtons = page.getByRole("button", { name: "Edit", exact: true });
  const count = await editButtons.count();
  let opened = false;
  for (let index = 0; index < count && !opened; index += 1) {
    await editButtons.nth(index).click();
    opened = await page
      .getByRole("dialog")
      .getByText("Edit App action")
      .isVisible()
      .catch(() => false);
    if (!opened) await page.keyboard.press("Escape");
  }
  expect(opened, "no app action on /console/tools").toBe(true);

  await page.getByRole("button", { name: "Refresh schema" }).click();
  await expect(page.getByTestId("provider-tool-retired")).toBeVisible();
  const result = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
  expect(blockingViolations(result)).toEqual([]);
});
