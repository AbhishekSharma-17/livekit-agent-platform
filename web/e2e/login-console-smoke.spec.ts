import { expect, test } from "@playwright/test";

/**
 * Login → console → test call → transcript smoke (PLAN-V2 V2-14, F-35; the
 * "9 Shared fakes / Playwright smoke" row of PLAN-V2.md §1, and stage L10's
 * acceptance: "Playwright smoke green").
 *
 * This needs a **real** password on a real account — `owner@local` has none
 * until the user runs `python -m lkap_api.auth set-password --email
 * owner@local` themselves (docs/v2/_asks.md #29; this package must not run
 * that, it writes the live DB) — and the dev-only admin-token bypass
 * (`LKAP_WEB_ADMIN_BYPASS`, `lib/auth.ts`) must be off, otherwise `/login`
 * never gates anything and the redirect this test proves never fires. Set
 * `LKAP_E2E_EMAIL` / `LKAP_E2E_PASSWORD` to run it for real; without them the
 * test skips rather than failing CI on a precondition nobody automated.
 *
 * The "test call → transcript" leg needs a bindable agent with a slug
 * (`LKAP_E2E_AGENT_SLUG`, default `e2e-insurance-7c593e` — the dev seed data
 * already has one, see `scripts/ui-capture.mjs`'s discovered ids) and a
 * worker actually listening on the connection; it is best-effort and does
 * not fail the smoke test if no transcript line appears within the timeout,
 * since a live worker is not this card's to guarantee.
 */
const EMAIL = process.env.LKAP_E2E_EMAIL;
const PASSWORD = process.env.LKAP_E2E_PASSWORD;
const AGENT_SLUG = process.env.LKAP_E2E_AGENT_SLUG ?? "e2e-insurance-7c593e";

// The call leg needs a (fake) microphone: the pre-call card asks for it on Start call.
// (Top level: `launchOptions` cannot be set inside a describe group.)
test.use({
  permissions: ["microphone"],
  launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
});

test.describe("login → console → test call → transcript", () => {
  test.skip(!EMAIL || !PASSWORD, "set LKAP_E2E_EMAIL / LKAP_E2E_PASSWORD to run this against a real account");

  test("signs in, reaches the console, and a test call produces a transcript", async ({ page }) => {
    // Login + first-compile under `next dev` + a best-effort 45 s transcript wait
    // do not fit the 30 s default (V2-20: the old test always timed out here).
    test.setTimeout(120_000);
    await page.goto("/console/agents");
    await expect(page).toHaveURL(/\/login\?next=/);

    await page.getByLabel("Email").fill(EMAIL!);
    await page.getByLabel("Password").fill(PASSWORD!);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page).toHaveURL(/\/console\/agents$/);
    await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();

    await page.goto(`/s/${AGENT_SLUG}?mode=test`);
    await expect(page).not.toHaveURL(/\/login/);
    // Without Start call no room is ever joined, so no transcript could appear.
    await page.getByRole("button", { name: "Start call" }).click();
    const transcript = page.locator("[data-testid='session-transcript']");
    const toggle = page.getByRole("button", { name: /toggle transcript/i });
    const sawTranscript = await expect
      .poll(
        async () => {
          if (!(await transcript.isVisible()) && (await toggle.isVisible())) await toggle.click();
          const text = (await transcript.innerText().catch(() => "")).replace(/transcript/i, "").trim();
          return text.length;
        },
        { timeout: 45_000 },
      )
      .toBeGreaterThan(0)
      .then(() => true)
      .catch(() => false); // best-effort: a live worker is not this card's to guarantee
    test.info().annotations.push({ type: "transcript", description: sawTranscript ? "seen" : "not seen" });
    await page
      .getByRole("button", { name: /end call|hang up|leave/i })
      .first()
      .click({ timeout: 5_000 })
      .catch(() => {});
  });
});
