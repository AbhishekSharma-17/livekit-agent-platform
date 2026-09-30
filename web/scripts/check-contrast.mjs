#!/usr/bin/env node
/**
 * WCAG 2.2 contrast gate for the design tokens in `src/app/globals.css`
 * (docs/ui/DESIGN-SYSTEM.md sections 2 and 11).
 *
 * - Light theme = every top-level rule whose selector list contains `:root`;
 *   dark theme = the light values overridden by every rule containing `.dark`
 *   (so the shared `:root, .dark` alias block counts for both).
 * - Resolves `var()` alias chains (`--primary` -> `--brand`).
 * - Gamut-maps OKLCH -> sRGB, then composites translucent colours (and the
 *   `tint` pairs, which model `bg-x/10`-style fills) over each page surface in
 *   sRGB gamma space (what the browser does) before computing ratios.
 * - Prints every pair and exits non-zero if any pair misses its target.
 *
 * Usage: `pnpm check:contrast` (or `node scripts/check-contrast.mjs [path]`).
 * Rule: if a pair fails, move the token's lightness, never the target.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parse, toGamut, wcagContrast } from "culori";

const toRgb = toGamut("rgb", "oklch");

/** Remove `/* … *\/` comments. */
export function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * Top-level rules of a stylesheet as `{ prelude, body }` (comments removed).
 * At-rules (`@theme inline`, `@layer base`, `@media …`) are returned too; their
 * body is the raw inner text.
 */
export function extractRules(css) {
  const src = stripComments(css);
  const rules = [];
  let i = 0;
  let preludeStart = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === ";") {
      // A statement at-rule (`@import …;`, `@custom-variant …;`).
      preludeStart = i + 1;
      i++;
      continue;
    }
    if (ch !== "{") {
      i++;
      continue;
    }
    const prelude = src.slice(preludeStart, i).trim();
    let depth = 1;
    let j = i + 1;
    for (; j < src.length && depth > 0; j++) {
      if (src[j] === "{") depth++;
      else if (src[j] === "}") depth--;
    }
    rules.push({ prelude, body: src.slice(i + 1, j - 1) });
    i = j;
    preludeStart = j;
  }
  return rules;
}

/** `--name: value;` declarations directly inside a rule body. */
export function declarations(body) {
  const vars = {};
  for (const decl of body.split(";")) {
    const m = decl.match(/^\s*(--[\w-]+)\s*:\s*([\s\S]+?)\s*$/);
    if (m) vars[m[1]] = m[2];
  }
  return vars;
}

/** Split a selector list on top-level commas. */
function selectorList(prelude) {
  return prelude.split(",").map((part) => part.trim().replace(/\s+/g, " "));
}

/**
 * Custom properties declared by every top-level rule whose selector list
 * contains `selector` exactly, merged in source order.
 */
export function extractBlock(css, selector) {
  const vars = {};
  let found = false;
  for (const rule of extractRules(css)) {
    if (rule.prelude.startsWith("@")) continue;
    if (!selectorList(rule.prelude).includes(selector)) continue;
    found = true;
    Object.assign(vars, declarations(rule.body));
  }
  if (!found) throw new Error(`No "${selector}" block found`);
  return vars;
}

/** The `@theme inline { … }` bridge declarations. */
export function extractBridge(css) {
  const vars = {};
  for (const rule of extractRules(css)) {
    if (/^@theme\b/.test(rule.prelude)) Object.assign(vars, declarations(rule.body));
  }
  return vars;
}

/** Light and dark variable maps (dark = light overridden by `.dark` rules). */
export function themes(css) {
  const light = extractBlock(css, ":root");
  const dark = { ...light, ...extractBlock(css, ".dark") };
  return { light, dark };
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

/** A parsed, gamut-mapped sRGB colour that keeps its alpha. */
export function toSrgb(value) {
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

const TEXT = 4.5;
const UI = 3;

const TEXT_ROLES = ["--foreground", "--text-secondary", "--text-tertiary"];
const TEXT_SURFACES = [
  "--background",
  "--card",
  "--popover",
  "--muted",
  "--muted-strong",
  "--sidebar",
  "--sidebar-hover",
  "--sidebar-active",
];
const UI_EDGES = ["--input", "--border-strong", "--ring"];
const UI_SURFACES = ["--background", "--card", "--popover", "--muted", "--sidebar"];
const LINK_SURFACES = [
  "--background",
  "--card",
  "--popover",
  "--muted",
  "--sidebar",
  "--sidebar-hover",
  "--sidebar-active",
  "--brand-subtle",
];
const STATUS = ["success", "warning", "info", "destructive"];
const CHARTS = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => `--chart-${n}`);

/**
 * Spec section 11 pairs, plus guards for legacy utilities that still read
 * a spec token as text. `on` is the surface; a translucent `on` (or a `tint`)
 * is flattened over each page base first.
 */
export const PAIRS = [
  // text roles on every surface that carries text
  ...TEXT_ROLES.flatMap((fg) => TEXT_SURFACES.map((on) => ({ fg, on, min: TEXT }))),
  // control edges and focus ring (WCAG 1.4.11)
  ...UI_EDGES.flatMap((fg) => UI_SURFACES.map((on) => ({ fg, on, min: UI, nonText: true }))),
  // accent: its foreground in every button state, and the accent as link / active-icon ink
  ...["--brand", "--brand-hover", "--brand-active"].map((on) => ({ fg: "--brand-foreground", on, min: TEXT })),
  ...LINK_SURFACES.map((on) => ({ fg: "--brand", on, min: TEXT })),
  // status scales: text on card, page and its subtle fill; the dot (solid) at 3:1;
  // the solid under its own foreground
  ...STATUS.flatMap((t) => [
    ...["--card", "--background", "--popover", `--${t}-subtle`].map((on) => ({ fg: `--${t}-text`, on, min: TEXT })),
    ...["--card", "--background", `--${t}-subtle`].map((on) => ({ fg: `--${t}-solid`, on, min: UI, nonText: true })),
    { fg: `--${t}-foreground`, on: `--${t}-solid`, min: TEXT },
  ]),
  { fg: "--destructive-foreground", on: "--destructive-hover", min: TEXT },
  // chart series read against the surfaces charts sit on
  ...CHARTS.flatMap((fg) => ["--card", "--background"].map((on) => ({ fg, on, min: UI, nonText: true }))),
  // legacy guards: `text-destructive` (the solid used as text) alone and on the
  // resting tint of the vendored destructive button, badge and menu item
  // (`bg-destructive/10` light, `dark:bg-destructive/20` dark), and the stage
  ...["--card", "--background", "--popover"].map((on) => ({ fg: "--destructive", on, min: TEXT })),
  { fg: "--destructive", on: "--destructive", tint: 0.1, min: TEXT, theme: "light" },
  { fg: "--destructive", on: "--destructive", tint: 0.2, min: TEXT, theme: "dark" },
  { fg: "--stage-foreground", on: "--stage", min: TEXT },
];

const BASES = ["--background", "--card"];

/**
 * Evaluate every pair for one theme; returns rows with ratio + pass flag.
 * Pairs tagged with a `theme` only run for that theme.
 */
export function evaluateTheme(vars, theme) {
  const color = (name) => toSrgb(resolveVar(vars, name));
  const rows = [];
  for (const pair of PAIRS) {
    if (pair.theme && theme && pair.theme !== theme) continue;
    const fg = color(pair.fg);
    const onRaw = color(pair.on);
    const on = pair.tint === undefined ? onRaw : { ...onRaw, alpha: pair.tint };
    // Flatten a translucent surface over each page base; an opaque surface
    // yields the same result for every base, so it is only reported once.
    const bases = on.alpha < 1 ? BASES : [null];
    for (const base of bases) {
      const baseColor = base ? color(base) : null;
      const surface = baseColor ? composite(on, baseColor) : on;
      const ink = fg.alpha < 1 ? composite(fg, surface) : fg;
      const ratio = wcagContrast(ink, surface);
      const onLabel = pair.tint === undefined ? pair.on : `${pair.on}/${Math.round(pair.tint * 100)}`;
      rows.push({
        pair: `${pair.fg} on ${onLabel}${base && base !== pair.on ? ` (over ${base})` : ""}`,
        ratio,
        min: pair.min,
        pass: ratio >= pair.min,
      });
    }
  }
  return rows;
}

export function checkContrast(css) {
  const { light, dark } = themes(css);
  return {
    light: evaluateTheme(light, "light"),
    dark: evaluateTheme(dark, "dark"),
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
