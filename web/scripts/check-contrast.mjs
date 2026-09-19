#!/usr/bin/env node
/**
 * WCAG 2.2 contrast gate for the design tokens in `src/app/globals.css`
 * (docs/UI_UX_SPEC.md §2.2, WP-0).
 *
 * - Reads the top-level `:root { … }` (light) and `.dark { … }` (dark) blocks.
 * - Resolves `var()` alias chains (`--success` → `--brand`, `--ring` → `--brand`).
 * - Gamut-maps OKLCH → sRGB, then composites alpha tokens (e.g. dark
 *   `--brand-soft`, `--border`) over `--background` *and* `--card` in sRGB
 *   gamma space (what the browser does) before computing ratios.
 * - Prints every pair and exits non-zero if any pair misses its target.
 *
 * Usage: `pnpm check:contrast` (or `node scripts/check-contrast.mjs [path]`).
 * Rule: if a pair fails, adjust the token's lightness, never the target.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parse, toGamut, wcagContrast } from "culori";

const toRgb = toGamut("rgb", "oklch");

/** Extract `--name: value;` declarations from the first top-level block matching `selector`. */
export function extractBlock(css, selector) {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(^|\\n)\\s*${escaped}\\s*\\{`, "g");
  const match = re.exec(stripped);
  if (!match) throw new Error(`No "${selector}" block found`);
  let depth = 1;
  let i = match.index + match[0].length;
  const start = i;
  for (; i < stripped.length && depth > 0; i++) {
    if (stripped[i] === "{") depth++;
    else if (stripped[i] === "}") depth--;
  }
  const body = stripped.slice(start, i - 1);
  const vars = {};
  for (const decl of body.split(";")) {
    const m = decl.match(/^\s*(--[\w-]+)\s*:\s*([\s\S]+?)\s*$/);
    if (m) vars[m[1]] = m[2];
  }
  return vars;
}

/** Resolve `var(--x)` chains to a concrete value. */
export function resolveVar(vars, name, seen = new Set()) {
  if (seen.has(name)) throw new Error(`Circular var() chain at ${name}`);
  seen.add(name);
  const raw = vars[name];
  if (raw === undefined) throw new Error(`Token ${name} is not defined`);
  const alias = raw.match(/^var\(\s*(--[\w-]+)\s*\)$/);
  return alias ? resolveVar(vars, alias[1], seen) : raw;
}

function toSrgb(value) {
  const parsed = parse(value);
  if (!parsed) throw new Error(`Cannot parse color "${value}"`);
  const alpha = parsed.alpha ?? 1;
  const rgb = toRgb({ ...parsed, alpha: 1 });
  return { mode: "rgb", r: rgb.r, g: rgb.g, b: rgb.b, alpha };
}

/** Source-over compositing in sRGB gamma space. `under` must be opaque. */
export function composite(over, under) {
  const a = over.alpha ?? 1;
  return {
    mode: "rgb",
    r: over.r * a + under.r * (1 - a),
    g: over.g * a + under.g * (1 - a),
    b: over.b * a + under.b * (1 - a),
    alpha: 1,
  };
}

/**
 * Pairs from docs/UI_UX_SPEC.md §2.2. `on` is the surface the foreground sits
 * on; when `on` is itself translucent it is first composited over `base`.
 * `bases` lists the page surfaces every translucent layer is flattened onto.
 */
const TONES = ["brand", "info", "success", "warning", "danger"];

export const PAIRS = [
  { fg: "--foreground", on: "--background", min: 7 },
  { fg: "--muted-foreground", on: "--background", min: 4.5 },
  { fg: "--muted-foreground", on: "--card", min: 4.5 },
  { fg: "--muted-foreground", on: "--muted", min: 4.5 },
  ...TONES.flatMap((t) => [
    { fg: `--${t}-text`, on: "--background", min: 4.5 },
    { fg: `--${t}-text`, on: `--${t}-soft`, min: 4.5 },
  ]),
  { fg: "--card-foreground", on: "--card", min: 4.5 },
  { fg: "--popover-foreground", on: "--popover", min: 4.5 },
  { fg: "--primary-foreground", on: "--primary", min: 4.5 },
  { fg: "--secondary-foreground", on: "--secondary", min: 4.5 },
  { fg: "--accent-foreground", on: "--accent", min: 4.5 },
  { fg: "--destructive-foreground", on: "--destructive", min: 4.5 },
  { fg: "--brand-foreground", on: "--brand", min: 4.5 },
  { fg: "--sidebar-foreground", on: "--sidebar", min: 4.5 },
  { fg: "--sidebar-primary-foreground", on: "--sidebar-primary", min: 4.5 },
  { fg: "--sidebar-accent-foreground", on: "--sidebar-accent", min: 4.5 },
  { fg: "--stage-foreground", on: "--stage", min: 4.5 },
  { fg: "--border", on: "--background", min: 1.5, nonText: true },
  { fg: "--input", on: "--background", min: 3, nonText: true },
  { fg: "--input", on: "--card", min: 3, nonText: true },
  { fg: "--ring", on: "--background", min: 3, nonText: true },
  { fg: "--ring", on: "--card", min: 3, nonText: true },
];

const BASES = ["--background", "--card"];

/** Evaluate every pair for one theme; returns rows with ratio + pass flag. */
export function evaluateTheme(vars) {
  const color = (name) => toSrgb(resolveVar(vars, name));
  const rows = [];
  for (const pair of PAIRS) {
    const fg = color(pair.fg);
    const on = color(pair.on);
    // Flatten a translucent surface over each page base; an opaque surface
    // yields the same result for every base, so it is only reported once.
    const bases = on.alpha < 1 ? BASES : [null];
    for (const base of bases) {
      const baseColor = base ? color(base) : null;
      const surface = baseColor ? composite(on, baseColor) : on;
      const ink = fg.alpha < 1 ? composite(fg, surface) : fg;
      const ratio = wcagContrast(ink, surface);
      rows.push({
        pair: `${pair.fg} on ${pair.on}${base && base !== pair.on ? ` (over ${base})` : ""}`,
        ratio,
        min: pair.min,
        pass: ratio >= pair.min,
      });
    }
  }
  return rows;
}

export function checkContrast(css) {
  const light = extractBlock(css, ":root");
  const darkOverrides = extractBlock(css, ".dark");
  const dark = { ...light, ...darkOverrides };
  return {
    light: evaluateTheme(light),
    dark: evaluateTheme(dark),
  };
}

function main() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const file = process.argv[2] ?? path.resolve(here, "../src/app/globals.css");
  const result = checkContrast(readFileSync(file, "utf8"));
  let failures = 0;
  for (const [theme, rows] of Object.entries(result)) {
    console.log(`\n${theme.toUpperCase()} theme`);
    for (const row of rows) {
      if (!row.pass) failures++;
      const mark = row.pass ? "pass" : "FAIL";
      console.log(
        `  ${mark}  ${row.ratio.toFixed(2).padStart(6)} : 1  (min ${row.min})  ${row.pair}`,
      );
    }
  }
  const total = result.light.length + result.dark.length;
  console.log(`\n${total - failures}/${total} pairs pass`);
  if (failures > 0) {
    console.error(`${failures} contrast pair(s) below target`);
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
