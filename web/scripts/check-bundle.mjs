#!/usr/bin/env node
/**
 * Session bundle budget (ruling R-V2-18, PLAN-V2 §8; UI_UX_SPEC-V2-AMENDMENTS
 * §0): the **First Load JS** of `/s/[slug]`, as `next build` prints it, must
 * stay at or under 620 kB. Size ("Size" column) is chunking-dependent and is
 * not the budget.
 *
 * Usage (from `web/`):
 *
 *   pnpm build 2>&1 | tee build.log
 *   node scripts/check-bundle.mjs build.log          # parse the route table
 *   node scripts/check-bundle.mjs --manifest [.next] # no log: estimate from the manifests
 *   pnpm build 2>&1 | node scripts/check-bundle.mjs  # or read the table on stdin
 *
 * Options: `--route=/s/[slug]` and `--budget-kb=620` override the defaults.
 *
 * Primary source is the printed route table (that is literally the metric the
 * ruling names). `--manifest` sums the gzipped size of every chunk
 * `.next/app-build-manifest.json` lists for the route's page and its layouts,
 * which reproduces Next's own computation closely enough to catch a
 * regression when no log was kept. Exit codes: 0 within budget, 1 over
 * budget, 2 when the figure could not be found at all (never a silent pass).
 */
import { existsSync, readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";

const args = process.argv.slice(2);
const flag = (name) => {
  const hit = args.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : undefined;
};
const ROUTE = flag("route") ?? "/s/[slug]";
const BUDGET_KB = Number(flag("budget-kb") ?? 620);
const useManifest = args.includes("--manifest");
const positional = args.filter((a) => !a.startsWith("--"));

const UNIT = { B: 1 / 1000, kB: 1, KB: 1, MB: 1000 };

/**
 * The route's First Load JS in kB from a `next build` route table, or `null`.
 * Rows look like `└ ƒ /s/[slug]    65.4 kB    589 kB` (Next 15): the last
 * size on the row is First Load JS.
 */
export function firstLoadFromLog(text, route = ROUTE) {
  // Strip ANSI colours; Next colours the sizes when stdout is a TTY.
  const clean = text.replace(/\u001b\[[0-9;]*m/g, "");
  for (const line of clean.split(/\r?\n/)) {
    const cells = line.trim().split(/\s+/);
    if (!cells.includes(route)) continue;
    const sizes = [...line.matchAll(/(\d+(?:\.\d+)?)\s*(B|kB|KB|MB)\b/g)];
    if (sizes.length === 0) continue;
    const [, value, unit] = sizes[sizes.length - 1];
    return Number(value) * UNIT[unit];
  }
  return null;
}

/** Estimate from `.next/app-build-manifest.json` (gzipped kB), or `null`. */
export function firstLoadFromManifest(nextDir, route = ROUTE) {
  const manifestPath = path.join(nextDir, "app-build-manifest.json");
  if (!existsSync(manifestPath)) return null;
  const pages = JSON.parse(readFileSync(manifestPath, "utf8")).pages ?? {};
  // App-router keys carry route groups: `/(session)/s/[slug]/page`.
  const strip = (key) => key.replace(/\/\([^)]+\)/g, "");
  const pageKey = Object.keys(pages).find((key) => strip(key) === `${route}/page`);
  if (!pageKey) return null;
  // The page plus every layout on its path (`/layout`, `/(session)/layout`, …).
  const segments = pageKey.split("/").slice(0, -1);
  const keys = [pageKey];
  for (let i = 0; i <= segments.length; i += 1) {
    const layoutKey = `${segments.slice(0, i).join("/")}/layout`;
    if (pages[layoutKey]) keys.push(layoutKey);
  }
  const files = new Set(keys.flatMap((key) => pages[key]).filter((file) => file.endsWith(".js")));
  let bytes = 0;
  for (const file of files) {
    const full = path.join(nextDir, file);
    if (existsSync(full)) bytes += gzipSync(readFileSync(full)).length;
  }
  return bytes / 1000;
}

async function readStdin() {
  if (process.stdin.isTTY) return "";
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

async function main() {
  let figure = null;
  let source = "";
  if (useManifest) {
    const nextDir = positional[0] ?? ".next";
    figure = firstLoadFromManifest(nextDir);
    source = `${path.join(nextDir, "app-build-manifest.json")} (gzipped estimate)`;
  } else {
    const text = positional[0] ? readFileSync(positional[0], "utf8") : await readStdin();
    figure = firstLoadFromLog(text);
    source = positional[0] ?? "stdin";
  }

  if (figure === null) {
    console.error(`check-bundle: no First Load JS figure for ${ROUTE} in ${source || "the input"}.`);
    process.exit(2);
  }

  const rounded = Math.round(figure * 10) / 10;
  const verdict = figure <= BUDGET_KB ? "ok" : "OVER BUDGET";
  console.log(`check-bundle: ${ROUTE} First Load JS ${rounded} kB (budget ${BUDGET_KB} kB, R-V2-18) — ${verdict} [${source}]`);
  if (figure > BUDGET_KB) {
    console.error(
      `check-bundle: ${ROUTE} is ${Math.round((figure - BUDGET_KB) * 10) / 10} kB over. Keep the flow canvas, pdf.js and anything under components/console/** out of the session bundle (R-V2-18).`,
    );
    process.exit(1);
  }
}

// Run only as a CLI, so tests can import the parsers.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  await main();
}
