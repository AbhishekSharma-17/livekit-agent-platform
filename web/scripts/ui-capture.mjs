#!/usr/bin/env node
/**
 * Visual QA capture + axe pass for the LKAP console/session/preview surfaces
 * (docs/UI_UX_SPEC.md §7.11 item 3).
 *
 * Usage (run from `web/`, against a running `pnpm dev` on :3000):
 *
 *   node scripts/ui-capture.mjs <outDir> [--only=<substring>] [--theme=light|dark] [--base=http://localhost:3000]
 *
 * - `<outDir>` is created if missing; screenshots land at
 *   `<outDir>/<viewport>-<name>[-dark|-light].png`, one `axe.json` collects
 *   every axe-core run, and `INDEX.md` lists every shot.
 * - `--only=<substring>` skips any shot whose name does not contain it.
 * - `--theme=light|dark` restricts console/session shots to one theme; both
 *   run when omitted. Preview scenes are unaffected (they carry their own
 *   `surfaces` per docs — see `src/components/preview/README.md`) unless a
 *   `--theme` value happens to be one of their surfaces, mirroring the
 *   console filter for consistency.
 * - Desktop console/session pages are captured full-page; the session
 *   surface (`h-dvh`, and every preview scene) and **all mobile shots** are
 *   viewport-only — a `fullPage` shot on mobile re-renders the sticky header
 *   mid-page (a known capture artifact from earlier work-package scripts).
 * - The dev server is checked before anything else: this script never
 *   screenshots a Next.js error page believing it is the real one.
 *
 * Route discovery: static console/session routes are hard-coded (the IA is
 * fixed), but agent/knowledge-base/session **ids** are fetched live from
 * `/api/console/**` so the script keeps working as the dev database changes,
 * and editor sections are read from `EDITOR_SECTIONS` inline below (kept in
 * sync with `src/components/console/agents/editor/sections.ts` — see the
 * NOTE at its declaration if that file's ids ever change).
 */
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const args = process.argv.slice(2);
const positional = args.filter((a) => !a.startsWith("--"));
const OUT = positional[0];
if (!OUT) {
  console.error("usage: node scripts/ui-capture.mjs <outDir> [--only=<substring>] [--theme=light|dark] [--base=http://localhost:3000]");
  process.exit(1);
}
const flag = (name) => {
  const hit = args.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : undefined;
};
const ONLY = flag("only");
const THEME_FILTER = flag("theme"); // "light" | "dark" | undefined
const BASE = flag("base") ?? "http://localhost:3000";

mkdirSync(OUT, { recursive: true });

/** The editor's section registry (docs/UI_UX_SPEC.md §7.4 item 3). Keep in
 * sync with `src/components/console/agents/editor/sections.ts`'s
 * `BUILTIN_SECTIONS` ids — that file is WP-3's, so this script does not
 * import it (the script must run standalone with `node`, no TS/JSX loader). */
const EDITOR_SECTIONS = [
  "providers",
  "instructions",
  "flow",
  "panel",
  "tools",
  "knowledge",
  "recording",
  "limits",
];

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  mobile: { width: 390, height: 844 },
};

/* -------------------------------------------------------------------------- */
/* Preflight                                                                   */
/* -------------------------------------------------------------------------- */

async function preflight() {
  let res;
  try {
    res = await fetch(BASE, { signal: AbortSignal.timeout(5000) });
  } catch (error) {
    console.error(`\nThe dev server does not appear to be up at ${BASE} (${error.message}).`);
    console.error("Start it with the preview_start tool (name: lkap-web) and re-run this script.\n");
    process.exit(2);
  }
  if (!res.ok) {
    console.error(`\n${BASE} responded ${res.status} — the dev server is up but not healthy. Aborting.\n`);
    process.exit(2);
  }
  console.log(`preflight ok: ${BASE} -> ${res.status}`);
}

/** Best-effort live lookups so shots track real dev data instead of stale ids. */
async function discoverIds() {
  const ids = { insuranceAgentId: null, genericAgentId: null, agentSlug: null, kbId: null, sessionId: null, connectionId: null };
  try {
    const agents = await fetch(`${BASE}/api/console/agents`).then((r) => r.json());
    const insurance = agents.items?.find((a) => a.pack_id === "insurance_claim" && a.published);
    const generic = agents.items?.find((a) => a.pack_id === "generic" && a.published);
    ids.insuranceAgentId = insurance?.id ?? agents.items?.[0]?.id ?? null;
    ids.genericAgentId = generic?.id ?? null;
    ids.agentSlug = insurance?.slug ?? agents.items?.[0]?.slug ?? null;
  } catch (error) {
    console.warn("warn: could not list agents —", error.message);
  }
  try {
    const kbs = await fetch(`${BASE}/api/console/knowledge-bases`).then((r) => r.json());
    ids.kbId = kbs.items?.[0]?.id ?? null;
  } catch (error) {
    console.warn("warn: could not list knowledge bases —", error.message);
  }
  try {
    const sessions = await fetch(`${BASE}/api/console/sessions?limit=5`).then((r) => r.json());
    ids.sessionId = sessions.items?.[0]?.id ?? null;
  } catch (error) {
    console.warn("warn: could not list sessions —", error.message);
  }
  try {
    // Read-only: this is the workspace's real connection(s). V2-13 never
    // clicks Test/Rotate/Delete from a capture script — this only opens the
    // detail page's Overview tab (no test call is fired on load).
    const connections = await fetch(`${BASE}/api/console/connections`).then((r) => r.json());
    ids.connectionId = connections.items?.find((c) => c.is_default)?.id ?? connections.items?.[0]?.id ?? null;
  } catch (error) {
    console.warn("warn: could not list connections —", error.message);
  }
  return ids;
}

/* -------------------------------------------------------------------------- */
/* Shot bookkeeping                                                            */
/* -------------------------------------------------------------------------- */

/** @type {{file: string, route: string, viewport: string, theme: string, group: string}[]} */
const shots = [];
/** @type {{route: string, viewport: string, theme: string, violations: unknown[]}[]} */
const axeResults = [];

function shouldRun(name) {
  return !ONLY || name.includes(ONLY);
}

function themeAllowed(theme) {
  return !THEME_FILTER || THEME_FILTER === theme;
}

async function shoot(page, { name, route, viewport, theme, group, fullPage }) {
  if (!shouldRun(name)) return;
  const file = `${viewport}-${name}.png`;
  await page.screenshot({ path: path.join(OUT, file), fullPage: Boolean(fullPage) });
  shots.push({ file, route, viewport, theme, group });
  console.log("shot", file);
}

async function runAxe(page, { route, viewport, theme }) {
  try {
    const result = await new AxeBuilder({ page }).analyze();
    const violations = result.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      helpUrl: v.helpUrl,
      nodes: v.nodes.length,
    }));
    axeResults.push({ route, viewport, theme, violations });
    const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    if (serious.length) {
      console.log(`axe: ${route} [${viewport}/${theme}] — ${serious.length} serious/critical`);
    }
  } catch (error) {
    console.warn(`warn: axe failed for ${route} —`, error.message);
  }
}

/* -------------------------------------------------------------------------- */
/* Console pages (real dev data — the API is healthy; no mocking needed)      */
/* -------------------------------------------------------------------------- */

async function gotoStable(page, url, { heading = true } = {}) {
  await page.goto(url, { waitUntil: "networkidle", timeout: 60_000 });
  if (heading) {
    await page.locator("h1, h2").first().waitFor({ timeout: 30_000 }).catch(() => {});
  }
  await page.evaluate(() => document.fonts.ready).catch(() => {});
  await page.waitForTimeout(300);
}

async function captureConsole(browser, ids) {
  const staticRoutes = [
    ["home", "/"],
    ["console-overview", "/console"],
    ["console-agents", "/console/agents"],
    ["console-agents-new", "/console/agents/new"],
    ["console-knowledge", "/console/knowledge"],
    ["console-sessions", "/console/sessions"],
    ["console-settings", "/console/settings"],
    ["console-tools", "/console/tools"],
    // V2-13: read-only navigations only — no Test/Rotate/Delete click ever
    // fires from this script, so the user's live default connection is
    // never touched by a capture run.
    ["console-connections", "/console/connections"],
    ["console-connections-new", "/console/connections/new"],
    ["console-providers", "/console/providers"],
    ["console-not-found", "/console/zzz-does-not-exist"],
  ];
  if (ids.kbId) staticRoutes.push(["console-kb-detail", `/console/knowledge/${ids.kbId}`]);
  if (ids.sessionId) staticRoutes.push(["console-session-detail", `/console/sessions/${ids.sessionId}`]);
  if (ids.connectionId) staticRoutes.push(["console-connection-detail", `/console/connections/${ids.connectionId}`]);

  for (const [vpName, vp] of Object.entries(VIEWPORTS)) {
    for (const theme of ["light", "dark"]) {
      if (!themeAllowed(theme)) continue;
      const context = await browser.newContext({ viewport: vp, deviceScaleFactor: 2 });
      await context.addInitScript((t) => window.localStorage.setItem("lkap-theme", t), theme);
      const page = await context.newPage();
      const suffix = theme === "dark" ? "-dark" : "";

      for (const [name, route] of staticRoutes) {
        const shotName = `${name}${suffix}`;
        if (!shouldRun(shotName)) continue;
        try {
          await gotoStable(page, BASE + route);
          await shoot(page, {
            name: shotName,
            route,
            viewport: vpName,
            theme,
            group: "console",
            fullPage: vpName === "desktop",
          });
          await runAxe(page, { route, viewport: vpName, theme });
        } catch (error) {
          console.log("fail", vpName, theme, name, String(error).slice(0, 160));
        }
      }

      if (ids.insuranceAgentId) {
        for (const section of EDITOR_SECTIONS) {
          const shotName = `console-editor-${section}${suffix}`;
          if (!shouldRun(shotName)) continue;
          try {
            await gotoStable(page, `${BASE}/console/agents/${ids.insuranceAgentId}?section=${section}`);
            await shoot(page, {
              name: shotName,
              route: `/console/agents/${ids.insuranceAgentId}?section=${section}`,
              viewport: vpName,
              theme,
              group: "editor",
              fullPage: vpName === "desktop",
            });
            await runAxe(page, { route: `editor:${section}`, viewport: vpName, theme });
          } catch (error) {
            console.log("fail", vpName, theme, "editor", section, String(error).slice(0, 160));
          }
        }
      }

      await context.close();
    }
  }
}

/* -------------------------------------------------------------------------- */
/* Session surface (dark, fixed — no theme toggle; real routes + not-found)   */
/* -------------------------------------------------------------------------- */

async function captureSession(browser, ids) {
  const routes = [["session-not-found", "/s/does-not-exist-zzz"]];
  if (ids.agentSlug) {
    routes.push(["session-precall", `/s/${ids.agentSlug}`]);
    routes.push(["session-precall-testmode", `/s/${ids.agentSlug}?mode=test`]);
  }

  for (const [vpName, vp] of Object.entries(VIEWPORTS)) {
    if (!themeAllowed("dark")) continue; // the session surface is dark, fixed
    const context = await browser.newContext({ viewport: vp, deviceScaleFactor: 2 });
    const page = await context.newPage();
    for (const [name, route] of routes) {
      if (!shouldRun(name)) continue;
      try {
        await gotoStable(page, BASE + route, { heading: false });
        await page.locator('[data-testid="pre-call-card"], [data-testid="session-unavailable"]').first().waitFor({ timeout: 15_000 }).catch(() => {});
        await shoot(page, { name, route, viewport: vpName, theme: "dark", group: "session", fullPage: false });
        await runAxe(page, { route, viewport: vpName, theme: "dark" });
      } catch (error) {
        console.log("fail", vpName, "session", name, String(error).slice(0, 160));
      }
    }
    await context.close();
  }
}

/* -------------------------------------------------------------------------- */
/* Preview scenes — driven entirely by `scene=list` (docs/UI_UX_SPEC.md §7.11)*/
/* -------------------------------------------------------------------------- */

async function fetchSceneList(page) {
  await page.goto(`${BASE}/console/preview/panels?scene=list`, { waitUntil: "networkidle", timeout: 30_000 });
  const text = await page.locator('[data-testid="scene-list"]').textContent();
  return JSON.parse(text ?? "[]");
}

async function capturePreview(browser) {
  const bootstrap = await browser.newContext();
  const bootstrapPage = await bootstrap.newPage();
  let entries;
  try {
    entries = await fetchSceneList(bootstrapPage);
  } catch (error) {
    console.error("fail: could not read scene=list —", String(error).slice(0, 200));
    entries = [];
  }
  await bootstrap.close();
  console.log(`preview: ${entries.length} combinations from scene=list`);

  for (const [vpName, vp] of Object.entries(VIEWPORTS)) {
    const context = await browser.newContext({ viewport: vp, deviceScaleFactor: 2 });
    const page = await context.newPage();
    for (const entry of entries) {
      for (const surface of entry.surfaces) {
        if (!themeAllowed(surface)) continue;
        const shotName = `preview-${entry.scene}-${entry.name}-${surface}`;
        if (!shouldRun(shotName)) continue;
        const url = `${BASE}/console/preview/panels?${entry.query}&surface=${surface}`;
        try {
          await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
          await page.evaluate(() => document.fonts.ready).catch(() => {});
          await page.waitForTimeout(250);
          await shoot(page, {
            name: shotName,
            route: `/console/preview/panels?${entry.query}&surface=${surface}`,
            viewport: vpName,
            theme: surface,
            group: "preview",
            fullPage: false,
          });
          await runAxe(page, { route: `preview:${entry.scene}:${entry.name}`, viewport: vpName, theme: surface });
        } catch (error) {
          console.log("fail", vpName, shotName, String(error).slice(0, 160));
        }
      }
    }
    await context.close();
  }
}

/* -------------------------------------------------------------------------- */
/* main                                                                        */
/* -------------------------------------------------------------------------- */

async function main() {
  await preflight();
  const ids = await discoverIds();
  console.log("discovered ids:", ids);

  const browser = await chromium.launch();
  try {
    await captureConsole(browser, ids);
    await captureSession(browser, ids);
    await capturePreview(browser);
  } finally {
    await browser.close();
  }

  writeFileSync(path.join(OUT, "axe.json"), JSON.stringify(axeResults, null, 2));

  const bySeverity = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  for (const result of axeResults) {
    for (const v of result.violations) {
      if (bySeverity[v.impact] !== undefined) bySeverity[v.impact] += 1;
    }
  }

  const indexLines = [
    "# UI capture — after WP-10",
    "",
    `Generated ${new Date().toISOString()} against \`${BASE}\`.`,
    "",
    `Axe summary — critical: ${bySeverity.critical}, serious: ${bySeverity.serious}, moderate: ${bySeverity.moderate}, minor: ${bySeverity.minor}. Full detail in \`axe.json\`.`,
    "",
    "| Screenshot | Route | Viewport | Theme/surface | Group |",
    "|---|---|---|---|---|",
    ...shots.map((s) => `| \`${s.file}\` | \`${s.route}\` | ${s.viewport} | ${s.theme} | ${s.group} |`),
  ];
  writeFileSync(path.join(OUT, "INDEX.md"), indexLines.join("\n") + "\n");

  console.log(`\n${shots.length} screenshots written to ${OUT}`);
  console.log(`axe: critical=${bySeverity.critical} serious=${bySeverity.serious} moderate=${bySeverity.moderate} minor=${bySeverity.minor}`);

  if (bySeverity.critical > 0 || bySeverity.serious > 0) {
    process.exitCode = 1;
  }
}

main();
