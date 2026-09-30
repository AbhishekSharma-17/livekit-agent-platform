import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * Render check (docs/ui/DESIGN-SYSTEM.md section 11; docs/ui/AUDIT.md section 6, V).
 *
 * Visits every route in the AUDIT route inventory (section 2) against an
 * already running app, in light and dark, at 390, 768 and 1440 px, and fails
 * on console errors (and uncaught page errors) and on horizontal page scroll
 * at 390 px. One screenshot per route, theme and width lands in
 * `RENDER_CHECK_OUT` (default `test-results/render-check`) as
 * `<route>-<theme>-<width>.png`, with `render-report.md` beside them.
 *
 * The browser also logs a failed request as a console error ("Failed to load
 * resource"). Those carry their URL here; the only ones that pass are a
 * not-found route's own document and the api reads documented to answer 404
 * for "nothing yet" (`EXPECTED_404`), which the console turns into an empty
 * state, not an error.
 *
 * Read-only: plain navigations only. Nothing is clicked or submitted, no call
 * is placed (`/s/[slug]` stops at the pre-call card; the embed's text channel,
 * which opens a session on load, is deliberately not visited). Detail routes
 * take the first id the dev api lists; an empty list is reported, not failed.
 *
 *   RENDER_CHECK_OUT=/some/dir pnpm exec playwright test e2e/render-check.spec.ts --workers=2
 */

const OUT = process.env.RENDER_CHECK_OUT ?? path.resolve(__dirname, "../test-results/render-check");
const RESULTS = path.join(OUT, ".results");
const THEMES = ["light", "dark"] as const;
const WIDTHS = [390, 768, 1440] as const;
const HEIGHT: Record<(typeof WIDTHS)[number], number> = { 390: 844, 768: 1024, 1440: 900 };

const SETTINGS_TABS = [
  "workspace",
  "appearance",
  "team",
  "api-keys",
  "ai-agents",
  "webhooks",
  "compliance",
  "knowledge-connections",
  "storage",
  "environment",
  "danger",
];

/**
 * Api reads that answer 404 for "none yet" by contract; the console maps the
 * 404 to `null` (`components/console/lib/api-hooks.ts`: `useProviderModel`,
 * `useLatestKbEvalRun`).
 */
const EXPECTED_404: RegExp[] = [
  /\/api\/console\/providers\/[^/]+\/models\/.+/,
  /\/api\/console\/knowledge-bases\/[^/]+\/evaluate\/latest$/,
];

interface RouteSpec {
  /** Path and query, or a function of the ids the api lists. */
  path: string | ((ids: Ids) => string | null);
  /** File-name stem. */
  name: string;
  /** Why the route could not be reached when `path` returns null. */
  needs?: string;
  /** The page itself answers 404 (a not-found route). */
  notFound?: boolean;
}

interface Ids {
  agent?: string;
  knowledgeBase?: string;
  dataset?: string;
  connection?: string;
  session?: string;
  publishedSlug?: string;
}

const ROUTES: RouteSpec[] = [
  { name: "home", path: "/" },
  { name: "login", path: "/login" },
  { name: "login-invite", path: "/login?invite=render-check" },
  { name: "not-found", path: "/render-check-missing-page", notFound: true },
  { name: "console", path: "/console" },
  { name: "console-agents", path: "/console/agents" },
  { name: "console-agents-new", path: "/console/agents/new" },
  { name: "console-agents-id", path: (ids) => (ids.agent ? `/console/agents/${ids.agent}` : null), needs: "an agent" },
  { name: "console-knowledge", path: "/console/knowledge" },
  {
    name: "console-knowledge-id",
    path: (ids) => (ids.knowledgeBase ? `/console/knowledge/${ids.knowledgeBase}` : null),
    needs: "a knowledge base",
  },
  { name: "console-tools", path: "/console/tools" },
  { name: "console-datasets", path: "/console/datasets" },
  { name: "console-datasets-id", path: (ids) => (ids.dataset ? `/console/datasets/${ids.dataset}` : null), needs: "a dataset" },
  { name: "console-connections", path: "/console/connections" },
  { name: "console-connections-new", path: "/console/connections/new" },
  {
    name: "console-connections-id",
    path: (ids) => (ids.connection ? `/console/connections/${ids.connection}` : null),
    needs: "a connection",
  },
  { name: "console-providers", path: "/console/providers" },
  { name: "console-keys", path: "/console/keys" },
  { name: "console-telephony", path: "/console/telephony" },
  { name: "console-sessions", path: "/console/sessions" },
  { name: "console-sessions-id", path: (ids) => (ids.session ? `/console/sessions/${ids.session}` : null), needs: "a session" },
  { name: "console-analytics", path: "/console/analytics" },
  ...SETTINGS_TABS.map((tab) => ({ name: `console-settings-${tab}`, path: `/console/settings?tab=${tab}` })),
  { name: "console-not-found", path: "/console/render-check-missing-page", notFound: true },
  { name: "console-preview-panels", path: "/console/preview/panels" },
  { name: "console-preview-styleguide", path: "/console/preview/styleguide" },
  { name: "s-slug", path: (ids) => (ids.publishedSlug ? `/s/${ids.publishedSlug}` : null), needs: "a published agent" },
  {
    name: "s-slug-test-mode",
    path: (ids) => (ids.publishedSlug ? `/s/${ids.publishedSlug}?mode=test` : null),
    needs: "a published agent",
  },
  {
    name: "s-slug-embed",
    path: (ids) => (ids.publishedSlug ? `/s/${ids.publishedSlug}?embed=1` : null),
    needs: "a published agent",
  },
  { name: "s-unavailable", path: "/s/render-check-missing-agent" },
];

interface Shot {
  theme: string;
  width: number;
  file: string;
  finalUrl: string;
  consoleErrors: string[];
  /** Failed requests the check accepts (documented 404s), for the report. */
  expectedFailures: string[];
  overflowPx: number;
}

interface RouteResult {
  name: string;
  path: string | null;
  unreachable?: string;
  shots: Shot[];
}

async function firstId(request: APIRequestContext, endpoint: string, pick?: (item: Record<string, unknown>) => boolean) {
  try {
    const response = await request.get(`/api/console/${endpoint}`, { timeout: 60_000 });
    if (!response.ok()) return undefined;
    const body = (await response.json()) as { items?: Record<string, unknown>[] } | Record<string, unknown>[];
    const items = Array.isArray(body) ? body : (body.items ?? []);
    const hit = items.find((item) => (pick ? pick(item) : true));
    return hit;
  } catch {
    return undefined;
  }
}

async function discoverIds(request: APIRequestContext): Promise<Ids> {
  const live = (item: Record<string, unknown>) => !item.archived_at;
  const agent = await firstId(request, "agents", live);
  const published = await firstId(request, "agents", (item) => live(item) && item.published === true);
  const kb = await firstId(request, "knowledge-bases");
  const dataset = await firstId(request, "datasets");
  const connection = await firstId(request, "connections");
  const session = await firstId(request, "sessions");
  return {
    agent: agent?.id as string | undefined,
    publishedSlug: published?.slug as string | undefined,
    knowledgeBase: kb?.id as string | undefined,
    dataset: dataset?.id as string | undefined,
    connection: connection?.id as string | undefined,
    session: session?.id as string | undefined,
  };
}

async function settle(page: Page) {
  // Polling screens never reach network idle; give them a bounded wait.
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  await page
    .locator("h1")
    .first()
    .waitFor({ timeout: 20_000 })
    .catch(() => undefined);
  // Let skeletons resolve and late effects log.
  await page.waitForTimeout(750);
}

function writeReport() {
  mkdirSync(RESULTS, { recursive: true });
  const results = readdirSync(RESULTS)
    .filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(readFileSync(path.join(RESULTS, file), "utf8")) as RouteResult)
    .sort((a, b) => ROUTES.findIndex((r) => r.name === a.name) - ROUTES.findIndex((r) => r.name === b.name));
  const shots = results.flatMap((r) => r.shots.map((s) => ({ route: r, shot: s })));
  const errors = shots.filter(({ shot }) => shot.consoleErrors.length > 0);
  const overflow = shots.filter(({ shot }) => shot.overflowPx > 0);
  const unreachable = results.filter((r) => r.unreachable);
  const redirected = shots.filter(({ route, shot }) => route.path && new URL(shot.finalUrl).pathname !== new URL(route.path, "http://x").pathname);

  const lines: string[] = [
    "# Render check",
    "",
    `Generated ${new Date().toISOString()} by \`web/e2e/render-check.spec.ts\` against ${process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000"}.`,
    "",
    `- Routes: ${results.length} (${results.length - unreachable.length} reached)`,
    `- Shots: ${shots.length} (${THEMES.length} themes x ${WIDTHS.length} widths per reached route)`,
    `- Shots with console errors: ${errors.length}`,
    `- Shots with horizontal page scroll: ${overflow.length} (fails the check only at 390 px)`,
    `- Routes that could not be reached: ${unreachable.length}`,
    "",
    "## Console errors",
    "",
  ];
  if (errors.length === 0) lines.push("None.", "");
  for (const { route, shot } of errors) {
    lines.push(`- \`${route.path}\` ${shot.theme} ${shot.width}px:`);
    for (const message of shot.consoleErrors) lines.push(`  - ${message.replace(/\s+/g, " ").slice(0, 400)}`);
  }
  const accepted = [...new Set(shots.flatMap(({ route, shot }) => (shot.expectedFailures ?? []).map((f) => `\`${route.path}\`: ${f}`)))];
  lines.push("", "## Accepted failed requests (documented 404s)", "");
  if (accepted.length === 0) lines.push("None.", "");
  for (const line of accepted) lines.push(`- ${line}`);
  lines.push("", "## Horizontal overflow", "");
  if (overflow.length === 0) lines.push("None.", "");
  for (const { route, shot } of overflow) lines.push(`- \`${route.path}\` ${shot.theme} ${shot.width}px: ${shot.overflowPx}px wider than the viewport`);
  lines.push("", "## Unreachable (needs auth or data)", "");
  if (unreachable.length === 0) lines.push("None.", "");
  for (const route of unreachable) lines.push(`- ${route.name}: ${route.unreachable}`);
  lines.push("", "## Redirected", "");
  if (redirected.length === 0) lines.push("None.", "");
  for (const { route, shot } of redirected) lines.push(`- \`${route.path}\` ${shot.theme} ${shot.width}px ended at \`${shot.finalUrl}\``);
  lines.push("", "## Routes", "", "| Route | Path | Shots |", "|---|---|---|");
  for (const route of results) lines.push(`| ${route.name} | \`${route.path ?? "-"}\` | ${route.shots.length} |`);
  writeFileSync(path.join(OUT, "render-report.md"), `${lines.join("\n")}\n`);
}

let ids: Ids | null = null;

test.beforeAll(async ({ request }) => {
  mkdirSync(RESULTS, { recursive: true });
  ids = await discoverIds(request);
});

test.afterAll(() => writeReport());

for (const route of ROUTES) {
  test(`renders ${route.name} in both themes at 390, 768 and 1440 px`, async ({ page }) => {
    test.setTimeout(600_000);
    const target = typeof route.path === "function" ? route.path(ids ?? {}) : route.path;
    const result: RouteResult = { name: route.name, path: target, shots: [] };
    const save = () => writeFileSync(path.join(RESULTS, `${route.name}.json`), JSON.stringify(result, null, 2));
    if (!target) {
      result.unreachable = `no ${route.needs ?? "data"} in the dev api`;
      save();
      test.skip(true, result.unreachable);
      return;
    }

    const consoleErrors: string[] = [];
    const expectedFailures: string[] = [];
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const text = message.text();
      if (!text.startsWith("Failed to load resource")) {
        consoleErrors.push(text);
        return;
      }
      const url = message.location().url;
      const isDocument = url !== "" && new URL(url).pathname === new URL(target, "http://x").pathname;
      const expected = / 404 /.test(text) && ((route.notFound && isDocument) || EXPECTED_404.some((re) => re.test(url)));
      (expected ? expectedFailures : consoleErrors).push(`${text} ${url}`);
    });
    page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

    const problems: string[] = [];
    for (const theme of THEMES) {
      for (const width of WIDTHS) {
        consoleErrors.length = 0;
        expectedFailures.length = 0;
        await page.setViewportSize({ width, height: HEIGHT[width] });
        await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
        await page.goto(target, { waitUntil: "load", timeout: 120_000 });
        await settle(page);
        const overflowPx = await page.evaluate(() => {
          const doc = document.documentElement;
          return Math.max(doc.scrollWidth, document.body.scrollWidth) - doc.clientWidth;
        });
        const file = `${route.name}-${theme}-${width}.png`;
        await page.screenshot({ path: path.join(OUT, file), fullPage: width !== 390 });
        const shot: Shot = {
          theme,
          width,
          file,
          finalUrl: page.url(),
          consoleErrors: [...consoleErrors],
          expectedFailures: [...expectedFailures],
          overflowPx: Math.max(0, overflowPx),
        };
        result.shots.push(shot);
        save();
        if (shot.consoleErrors.length > 0) problems.push(`${theme} ${width}px console errors: ${shot.consoleErrors.join(" | ")}`);
        if (width === 390 && shot.overflowPx > 1) problems.push(`${theme} 390px: page scrolls ${shot.overflowPx}px sideways`);
      }
    }
    expect(problems, `render check on ${target}`).toEqual([]);
  });
}
