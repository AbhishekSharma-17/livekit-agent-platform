import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Accessibility floor for V6-19's three new dialogs (the card's own acceptance: "axe checks
 * on the three dialogs — upload, Add-kit, dataset tool editor"). `a11y.spec.ts` only ever
 * navigates (it never clicks — its own docstring says so), so opening a dialog needed its own
 * spec; this follows the same shape (no critical/serious axe finding), against a running
 * `pnpm dev`/`next start` with the admin bypass on (`a11y.spec.ts`'s own precondition — every
 * `/console/*` route there loads with no login step).
 *
 * The upload dialog and the dataset tool editor live on `/console/tools` and `/console/datasets`,
 * neither needing a specific agent, so those two always run. The Add-kit dialog lives on one
 * agent's Tools tab (`/console/agents/{id}?section=tools`) — needs a real database id, which
 * this package cannot seed itself (`LKAP_E2E_AGENT_ID`, unset by default: skip rather than
 * fail CI on a precondition nobody automated, `login-console-smoke.spec.ts`'s own pattern).
 */
const AGENT_ID = process.env.LKAP_E2E_AGENT_ID;

function blockingViolations(result: Awaited<ReturnType<AxeBuilder["analyze"]>>) {
  return result.violations
    .filter((violation) => violation.impact === "critical" || violation.impact === "serious")
    .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`);
}

test("no critical or serious axe findings in the upload-a-lookup-table dialog", async ({ page }) => {
  await page.goto("/console/datasets", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Upload a lookup table" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const result = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
  expect(blockingViolations(result)).toEqual([]);
});

test("no critical or serious axe findings in the dataset tool editor dialog", async ({ page }) => {
  await page.goto("/console/tools", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Add lookup tool" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const result = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
  expect(blockingViolations(result)).toEqual([]);
});

test("no critical or serious axe findings in the Add-kit dialog", async ({ page }) => {
  test.skip(!AGENT_ID, "set LKAP_E2E_AGENT_ID to a real agent id to run this");
  await page.goto(`/console/agents/${AGENT_ID}?section=tools`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Add to this agent" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const result = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
  expect(blockingViolations(result)).toEqual([]);
});
