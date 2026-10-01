#!/usr/bin/env node
/**
 * Design lint (docs/ui/DESIGN-SYSTEM.md section 11). A grep-level gate over
 * `src/**` that fails on:
 *
 *   colour-literal   hex, rgb(), hsl(), oklch() … colours outside the token file
 *   raw-shadow       px box-shadow / shadow-[…px…] outside the token file
 *   native-select    a native <select>
 *   browser-dialog   window.confirm / window.alert / window.prompt / alert(
 *   stroke-width     a per-icon strokeWidth / absoluteStrokeWidth
 *   sparkle-icon     Sparkle / Sparkles / Wand / WandSparkles … icons
 *   palette-class    Tailwind palette utilities (bg-slate-500, text-white …)
 *   opacity-colour   opacity-modified colour utilities (bg-muted/50 …)
 *   css-comment      a stray `*\/` or a nested `/*` in a CSS comment
 *   token-bridge     an @theme bridge var() that points at no token
 *   legacy-token     a legacy token alias (text-muted-foreground, bg-brand-soft,
 *                    shadow-md, rounded-xl, var(--danger) …) instead of its spec
 *                    name (docs/ui/AUDIT.md section 4, decision O2)
 *
 * Today's violations are listed, per rule and per file, in
 * `scripts/design-lint-allowlist.txt`. The allowlist is a ratchet: a file may
 * not exceed its allowance, and a file that improves must lower its line
 * (`--shrink` does that for you). Screen packages shrink it to zero; the
 * `exempt` lines cover leave-alone code (docs/ui/AUDIT.md section 8).
 *
 * Usage: `pnpm lint:design` (`node scripts/design-lint.mjs [--shrink]`).
 */
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { extractBridge, themes } from "./check-contrast.mjs";

export const TOKEN_FILE = "src/app/globals.css";
const SOURCE_EXT = /\.(tsx?|jsx?|mjs|css)$/;

const PALETTE =
  "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|black|white";
const PREFIX =
  "bg|text|border(?:-[trblxyse])?|ring|ring-offset|fill|stroke|from|via|to|outline|divide|shadow|decoration|accent|caret|placeholder";

export const RULES = {
  "colour-literal": "Colour literal outside the token file: use a token (var(--x) or its utility)",
  "raw-shadow": "Raw px shadow outside the token file: use shadow-raised / shadow-overlay / shadow-modal",
  "native-select": "Native <select>: use the Select primitive",
  "browser-dialog": "Browser dialog: use ConfirmDialog / an inline two-step, never window.confirm or alert",
  "stroke-width": "Per-icon stroke width: the global .lucide rule sets it; override size only",
  "sparkle-icon": "Sparkle / magic-wand icon: pick a literal icon from the intent map",
  "palette-class": "Palette utility class: use a token utility",
  "opacity-colour": "Opacity-modified colour utility: use a token (e.g. muted, brand-subtle)",
  "css-comment": "CSS comment hazard: a stray */ or a nested /* can break the stylesheet",
  "token-bridge": "@theme bridge points at an undefined token",
  "legacy-token": "Legacy token alias: use the spec name (docs/ui/AUDIT.md section 4)",
};

/**
 * Legacy colour names that are only `var()` aliases of a spec token (docs/ui/AUDIT.md section 4).
 * The session-only survivors stay out on purpose: `stage`, `--radius-2xl`, `--ease-in-out`
 * (state meter) and `--ease-drawer` (session bottom sheet) have no spec equivalent.
 */
export const LEGACY_COLOURS = [
  "card-foreground",
  "popover-foreground",
  "muted-foreground",
  "accent-foreground",
  "accent",
  "secondary-foreground",
  "secondary",
  "primary-foreground",
  "primary",
  "destructive",
  "brand-text",
  "brand-soft",
  "brand-line",
  "success-soft",
  "success",
  "warning-soft",
  "warning",
  "info-soft",
  "info",
  "danger-soft",
  "danger-text",
  "danger",
  "sidebar-foreground",
  "sidebar-primary-foreground",
  "sidebar-primary",
  "sidebar-accent-foreground",
  "sidebar-accent",
  "sidebar-border",
  "sidebar-ring",
];

/**
 * Blank out `//` and `/* *\/` comments (keeping newlines and columns) while
 * leaving string contents alone. `css` mode knows no `//` comments.
 */
export function blankComments(text, { css = false } = {}) {
  const out = text.split("");
  let i = 0;
  let quote = null;
  while (i < text.length) {
    const ch = text[i];
    const next = text[i + 1];
    if (quote) {
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === quote || (ch === "\n" && quote !== "`")) quote = null;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'" || (ch === "`" && !css)) {
      quote = ch;
      i++;
      continue;
    }
    if (ch === "/" && next === "*") {
      const end = text.indexOf("*/", i + 2);
      const stop = end === -1 ? text.length : end + 2;
      for (let k = i; k < stop; k++) if (out[k] !== "\n") out[k] = " ";
      i = stop;
      continue;
    }
    if (!css && ch === "/" && next === "/" && text[i - 1] !== "\\") {
      let k = i;
      for (; k < text.length && text[k] !== "\n"; k++) out[k] = " ";
      i = k;
      continue;
    }
    i++;
  }
  return out.join("");
}

/** Colour names the Tailwind bridge defines (`--color-<name>`), longest first. */
export function bridgeColourNames(css) {
  return Object.keys(extractBridge(css))
    .filter((key) => key.startsWith("--color-"))
    .map((key) => key.slice("--color-".length))
    .sort((a, b) => b.length - a.length);
}

function escapeRe(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Build the per-rule regexes. */
export function buildMatchers(colourNames) {
  const tokens = colourNames.map(escapeRe).join("|");
  return [
    {
      rule: "colour-literal",
      skipTokenFile: true,
      re: /(?<![\w&#/.$-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])/g,
    },
    { rule: "colour-literal", skipTokenFile: true, re: /(?<![\w-])(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\(/g },
    { rule: "raw-shadow", skipTokenFile: true, re: /\b(?:box-shadow|boxShadow)\s*:\s*[^;}\n]*?\d+(?:\.\d+)?px/g },
    { rule: "raw-shadow", skipTokenFile: true, re: /(?<![\w-])(?:inset-|drop-)?shadow-\[[^\]\s]*\d+(?:\.\d+)?px[^\]\s]*\]/g },
    { rule: "native-select", tsxOnly: true, re: /<select(?=[\s>/])/g },
    { rule: "browser-dialog", re: /\bwindow\.(?:confirm|alert|prompt)\s*\(/g },
    { rule: "browser-dialog", re: /(?<![\w.$]|function\s)alert\s*\(/g },
    { rule: "stroke-width", tsxOnly: true, re: /\b(?:strokeWidth\s*[=:]|absoluteStrokeWidth\b)|\bstroke-width=/g },
    {
      rule: "sparkle-icon",
      re: /\b(?:Lucide)?(?:Sparkles?|WandSparkles|Wand2|Wand|MagicWand)(?:Icon)?\b/g,
    },
    {
      rule: "palette-class",
      re: new RegExp(`(?<![\\w-])(?:${PREFIX})-(?:${PALETTE})(?:-\\d{2,3})?(?:\\/(?:\\d+|\\[[^\\]]+\\]))?(?![\\w-])`, "g"),
    },
    {
      rule: "opacity-colour",
      re: new RegExp(`(?<![\\w-])(?:${PREFIX})-(?:${tokens})\\/(?:\\d+|\\[[^\\]]+\\])(?![\\w-])`, "g"),
    },
    {
      rule: "legacy-token",
      skipTokenFile: true,
      re: new RegExp(`(?<![\\w-])(?:${PREFIX})-(?:${LEGACY_COLOURS.join("|")})(?![\\w-])`, "g"),
    },
    {
      rule: "legacy-token",
      skipTokenFile: true,
      re: /(?<![\w-])(?:shadow-(?:sm|md|lg)|rounded(?:-[trblse]{1,2})?-(?:xs|md|xl)|ease-out)(?![\w-])/g,
    },
    {
      rule: "legacy-token",
      skipTokenFile: true,
      re: new RegExp(
        `var\\(\\s*--(?:${LEGACY_COLOURS.join("|")}|shadow-(?:sm|md|lg)|radius-(?:xs|md|xl)|dur-\\d|ease-out)\\s*[,)]`,
        "g",
      ),
    },
  ];
}

/** Violations `{ rule, line, col, match }` in one source file. */
export function lintSource(relPath, text, matchers) {
  const isCss = relPath.endsWith(".css");
  const isTsx = /\.(tsx|jsx)$/.test(relPath);
  const code = blankComments(text, { css: isCss });
  const lineStarts = [0];
  for (let i = 0; i < code.length; i++) if (code[i] === "\n") lineStarts.push(i + 1);
  const locate = (index) => {
    let lo = 0;
    let hi = lineStarts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (lineStarts[mid] <= index) lo = mid;
      else hi = mid - 1;
    }
    return { line: lo + 1, col: index - lineStarts[lo] + 1 };
  };
  const violations = [];
  for (const matcher of matchers) {
    if (matcher.skipTokenFile && relPath === TOKEN_FILE) continue;
    if (matcher.tsxOnly && !isTsx) continue;
    for (const m of code.matchAll(matcher.re)) {
      violations.push({ rule: matcher.rule, ...locate(m.index), match: m[0].trim() });
    }
  }
  if (isCss) violations.push(...lintCssComments(text));
  return violations;
}

/**
 * CSS comment safety: after closing each comment at its first `*\/`, no `*\/`
 * may remain outside a comment, and no comment body may contain `/*` (a sign
 * that something like a `src/**\/*.ts` glob ended the comment early).
 */
export function lintCssComments(text) {
  const violations = [];
  const at = (index) => {
    const before = text.slice(0, index);
    const line = before.split("\n").length;
    return { line, col: index - before.lastIndexOf("\n") };
  };
  let i = 0;
  let quote = null;
  while (i < text.length) {
    const ch = text[i];
    if (quote) {
      if (ch === "\\") i++;
      else if (ch === quote || ch === "\n") quote = null;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      i++;
      continue;
    }
    if (ch === "/" && text[i + 1] === "*") {
      const end = text.indexOf("*/", i + 2);
      if (end === -1) {
        violations.push({ rule: "css-comment", ...at(i), match: "unterminated /*" });
        break;
      }
      const nested = text.indexOf("/*", i + 2);
      if (nested !== -1 && nested < end) {
        violations.push({ rule: "css-comment", ...at(nested), match: "/* inside a comment" });
      }
      i = end + 2;
      continue;
    }
    if (ch === "*" && text[i + 1] === "/") {
      violations.push({ rule: "css-comment", ...at(i), match: "stray */" });
    }
    i++;
  }
  return violations;
}

/** Bridge entries whose var() target is defined in neither theme. */
export function lintBridge(css) {
  const { light, dark } = themes(css);
  const external = new Set(["--font-inter", "--font-hand", "--font-hand-label"]);
  const violations = [];
  for (const [key, value] of Object.entries(extractBridge(css))) {
    const target = value.match(/^var\(\s*(--[\w-]+)\s*\)$/)?.[1];
    if (!target || external.has(target)) continue;
    if (light[target] === undefined || dark[target] === undefined) {
      violations.push({ rule: "token-bridge", line: 1, col: 1, match: `${key} -> ${target}` });
    }
  }
  return violations;
}

/**
 * Allowlist lines:
 *   exempt <rule|*> <path-prefix> [reason…]   permanent (leave-alone code)
 *   allow  <rule> <count> <path>               ratchet (shrinks to zero)
 * `#` starts a comment.
 */
export function parseAllowlist(text) {
  const exempt = [];
  const allow = new Map();
  const errors = [];
  text.split("\n").forEach((raw, index) => {
    const line = raw.replace(/#.*$/, "").trim();
    if (!line) return;
    const parts = line.split(/\s+/);
    if (parts[0] === "exempt" && parts.length >= 3) {
      exempt.push({ rule: parts[1], prefix: parts[2], line: index + 1 });
    } else if (parts[0] === "allow" && parts.length === 4 && /^\d+$/.test(parts[2])) {
      const key = `${parts[1]} ${parts[3]}`;
      if (allow.has(key)) errors.push(`allowlist line ${index + 1}: duplicate entry for ${key}`);
      allow.set(key, { rule: parts[1], path: parts[3], count: Number(parts[2]), line: index + 1 });
    } else {
      errors.push(`allowlist line ${index + 1}: cannot parse "${raw.trim()}"`);
    }
    const rule = parts[1];
    if (rule && rule !== "*" && !(rule in RULES)) errors.push(`allowlist line ${index + 1}: unknown rule "${rule}"`);
  });
  return { exempt, allow, errors };
}

function isExempt(exempt, rule, relPath) {
  return exempt.some((entry) => (entry.rule === "*" || entry.rule === rule) && relPath.startsWith(entry.prefix));
}

/** Every lintable file under `src/`, as paths relative to `root`. */
export function sourceFiles(root) {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      if (name === "node_modules" || name.startsWith(".")) continue;
      const full = path.join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (SOURCE_EXT.test(name)) files.push(path.relative(root, full).split(path.sep).join("/"));
    }
  };
  walk(path.join(root, "src"));
  return files.sort();
}

/**
 * Lint `files` (a map of relative path -> text) against the allowlist.
 * Returns `{ errors, counts, allowed }`: `errors` are human-readable lines;
 * `counts` maps "rule path" -> current count for non-exempt violations.
 */
export function runDesignLint({ files, tokenCss, allowlistText }) {
  const { exempt, allow, errors: parseErrors } = parseAllowlist(allowlistText);
  const errors = [...parseErrors];
  const matchers = buildMatchers(bridgeColourNames(tokenCss));
  const counts = new Map();
  const found = new Map();
  for (const [relPath, text] of Object.entries(files)) {
    for (const v of lintSource(relPath, text, matchers)) {
      if (isExempt(exempt, v.rule, relPath)) continue;
      const key = `${v.rule} ${relPath}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
      if (!found.has(key)) found.set(key, []);
      found.get(key).push(v);
    }
  }
  for (const v of lintBridge(tokenCss)) errors.push(`${TOKEN_FILE}: ${v.rule}: ${v.match} (${RULES[v.rule]})`);

  for (const [key, count] of counts) {
    const entry = allow.get(key);
    const allowed = entry?.count ?? 0;
    if (count > allowed) {
      const [rule, relPath] = key.split(" ");
      const list = found.get(key).map((v) => `    ${relPath}:${v.line}:${v.col}  ${v.match}`);
      errors.push(
        `${relPath}: ${count} ${rule} violation(s), ${allowed} allowed. ${RULES[rule]}\n${list.join("\n")}`,
      );
    }
  }
  for (const [key, entry] of allow) {
    const count = counts.get(key) ?? 0;
    if (count < entry.count) {
      errors.push(
        `allowlist line ${entry.line}: ${entry.path} now has ${count} ${entry.rule} violation(s); ` +
          (count === 0 ? "remove the line" : `lower the allowance to ${count}`) +
          " (or run `pnpm lint:design --shrink`)",
      );
    }
  }
  const allowed = {};
  for (const [key, entry] of allow) {
    const count = Math.min(counts.get(key) ?? 0, entry.count);
    allowed[entry.rule] ??= { files: 0, violations: 0 };
    allowed[entry.rule].files += 1;
    allowed[entry.rule].violations += count;
  }
  return { errors, counts, allowed };
}

/** Rewrite the ratchet lines down to today's counts (never up, never new). */
export function shrinkAllowlist(allowlistText, counts) {
  return allowlistText
    .split("\n")
    .flatMap((raw) => {
      const parts = raw.replace(/#.*$/, "").trim().split(/\s+/);
      if (parts[0] !== "allow" || parts.length !== 4) return [raw];
      const current = counts.get(`${parts[1]} ${parts[3]}`) ?? 0;
      const allowed = Number(parts[2]);
      if (current >= allowed) return [raw];
      if (current === 0) return [];
      return [raw.replace(/^(\s*allow\s+\S+\s+)\d+/, `$1${current}`)];
    })
    .join("\n");
}

function main() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const allowlistPath = path.join(root, "scripts/design-lint-allowlist.txt");
  const allowlistText = readFileSync(allowlistPath, "utf8");
  const files = Object.fromEntries(sourceFiles(root).map((rel) => [rel, readFileSync(path.join(root, rel), "utf8")]));
  const tokenCss = files[TOKEN_FILE];
  let result = runDesignLint({ files, tokenCss, allowlistText });

  if (process.argv.includes("--shrink")) {
    const next = shrinkAllowlist(allowlistText, result.counts);
    if (next !== allowlistText) {
      writeFileSync(allowlistPath, next);
      console.log("design-lint: lowered the allowlist to today's counts");
      result = runDesignLint({ files, tokenCss, allowlistText: next });
    }
  }

  const summary = Object.entries(result.allowed)
    .sort()
    .map(([rule, { files: n, violations }]) => `  ${rule.padEnd(15)} ${String(violations).padStart(4)} in ${n} file(s)`);
  console.log(`design-lint: ${Object.keys(files).length} files checked`);
  if (summary.length) console.log(`allowlisted (shrink these to zero):\n${summary.join("\n")}`);
  if (result.errors.length) {
    console.error(`\n${result.errors.join("\n\n")}\n\ndesign-lint: ${result.errors.length} problem(s)`);
    process.exit(1);
  }
  console.log("design-lint: pass");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
