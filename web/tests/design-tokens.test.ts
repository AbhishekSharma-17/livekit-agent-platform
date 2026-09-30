import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { declarations, extractBlock, extractBridge, extractRules, resolveVar, themes } from "../scripts/check-contrast.mjs";

/**
 * docs/ui/DESIGN-SYSTEM.md section 2: every token is defined in `:root`
 * (light), in `.dark` (dark) and bridged in `@theme inline`. A token missing
 * from any of the three is a bug.
 */
const css = readFileSync(path.resolve(__dirname, "../src/app/globals.css"), "utf8");

const STATUS = ["success", "warning", "info", "destructive"].flatMap((tone) =>
  ["solid", "text", "subtle", "border", "foreground"].map((part) => `--${tone}-${part}`),
);

/** Colour tokens bridge to `--color-<name>`. */
const COLOR_TOKENS = [
  "--background",
  "--card",
  "--popover",
  "--muted",
  "--muted-strong",
  "--border",
  "--border-strong",
  "--input",
  "--foreground",
  "--text-secondary",
  "--text-tertiary",
  "--text-disabled",
  "--sidebar",
  "--sidebar-hover",
  "--sidebar-active",
  "--overlay",
  "--scrim",
  "--brand",
  "--brand-hover",
  "--brand-active",
  "--brand-foreground",
  "--brand-subtle",
  "--brand-border",
  "--ring",
  ...STATUS,
  "--destructive-hover",
  ...[1, 2, 3, 4, 5, 6, 7, 8].map((n) => `--chart-${n}`),
];

/** Every other spec token, with its Tailwind bridge key. */
const OTHER_TOKENS: Record<string, string> = {
  "--elevation-raised": "--shadow-raised",
  "--elevation-overlay": "--shadow-overlay",
  "--elevation-modal": "--shadow-modal",
  "--focus-shadow": "--shadow-focus",
  "--radius-sm": "--radius-sm",
  "--radius": "--radius",
  "--radius-lg": "--radius-lg",
  "--radius-dialog": "--radius-dialog",
  "--radius-pill": "--radius-pill",
  "--duration-fast": "--duration-fast",
  "--duration-base": "--duration-base",
  "--duration-slow": "--duration-slow",
  "--ease-entrance": "--ease-entrance",
  "--layout-topbar": "--spacing-topbar",
  "--layout-panel-inset": "--spacing-panel-inset",
  "--layout-bottombar": "--spacing-bottombar",
};

const SPEC_TOKENS: Array<[string, string]> = [
  ...COLOR_TOKENS.map((token): [string, string] => [token, `--color-${token.slice(2)}`]),
  ...Object.entries(OTHER_TOKENS),
];

const light = extractBlock(css, ":root") as Record<string, string>;
const dark = extractBlock(css, ".dark") as Record<string, string>;
const bridge = extractBridge(css) as Record<string, string>;

describe("design tokens (globals.css)", () => {
  it.each(SPEC_TOKENS)("%s is defined in :root, .dark and the @theme bridge", (token, bridgeKey) => {
    expect(light[token], `${token} missing from :root`).toBeDefined();
    expect(dark[token], `${token} missing from .dark`).toBeDefined();
    expect(bridge[bridgeKey], `${bridgeKey} missing from @theme inline`).toBe(`var(${token})`);
  });

  it("gives every colour and elevation token a literal value in each theme's own block", () => {
    const own = (selector: string) =>
      Object.assign(
        {},
        ...(extractRules(css) as Array<{ prelude: string; body: string }>)
          .filter((rule) => rule.prelude === selector)
          .map((rule) => declarations(rule.body) as Record<string, string>),
      ) as Record<string, string>;
    const lightOwn = own(":root");
    const darkOwn = own(".dark");
    for (const token of [...COLOR_TOKENS, "--elevation-raised", "--elevation-overlay", "--elevation-modal"]) {
      expect(lightOwn[token], `${token} light literal`).toMatch(/oklch\(/);
      expect(darkOwn[token], `${token} dark literal`).toMatch(/oklch\(/);
      expect(darkOwn[token], `${token} dark value`).not.toBe(lightOwn[token]);
    }
  });

  it("declares every light token in the dark theme too", () => {
    const missing = Object.keys(light).filter((name) => dark[name] === undefined);
    expect(missing).toEqual([]);
  });

  it("points every bridge var() at a token defined in both themes", () => {
    const { light: lightVars, dark: darkVars } = themes(css) as {
      light: Record<string, string>;
      dark: Record<string, string>;
    };
    // next/font sets these on <html>; the hand fonts come from the session layout.
    const external = new Set(["--font-inter", "--font-hand", "--font-hand-label"]);
    const unresolved: string[] = [];
    for (const [key, value] of Object.entries(bridge)) {
      const target = value.match(/^var\(\s*(--[\w-]+)\s*\)$/)?.[1];
      expect(target, `${key} should bridge a single var()`).toBeDefined();
      if (!target || external.has(target)) continue;
      for (const vars of [lightVars, darkVars]) {
        try {
          resolveVar(vars, target);
        } catch {
          unresolved.push(`${key} -> ${target}`);
        }
      }
    }
    expect(unresolved).toEqual([]);
  });

  it("keeps old names as aliases of the new scale", () => {
    const aliases: Record<string, string> = {
      "--muted-foreground": "var(--text-secondary)",
      "--primary": "var(--brand)",
      "--success": "var(--success-solid)",
      "--danger": "var(--destructive-solid)",
      "--danger-text": "var(--destructive-text)",
      "--destructive": "var(--destructive-solid)",
      "--brand-soft": "var(--brand-subtle)",
      "--brand-line": "var(--brand-border)",
      "--shadow-md": "var(--elevation-overlay)",
      "--radius-xl": "var(--radius-dialog)",
      "--dur-2": "var(--duration-base)",
      "--ease-out": "var(--ease-entrance)",
    };
    for (const [legacy, target] of Object.entries(aliases)) {
      expect(light[legacy], legacy).toBe(target);
      expect(dark[legacy], legacy).toBe(target);
    }
  });

  it("uses the spec's theme-invariant values", () => {
    expect(light["--radius-sm"]).toBe("6px");
    expect(light["--radius"]).toBe("8px");
    expect(light["--radius-lg"]).toBe("12px");
    expect(light["--radius-dialog"]).toBe("14px");
    expect(light["--radius-pill"]).toBe("999px");
    expect(light["--duration-fast"]).toBe("100ms");
    expect(light["--duration-base"]).toBe("150ms");
    expect(light["--duration-slow"]).toBe("220ms");
    expect(light["--ease-entrance"]).toBe("cubic-bezier(0.16, 1, 0.3, 1)");
    expect(light["--layout-topbar"]).toBe("56px");
    expect(light["--layout-panel-inset"]).toBe("8px");
    expect(light["--layout-bottombar"]).toBe("60px");
  });
});

describe("global rules (globals.css)", () => {
  const flat = css.replace(/\s+/g, " ");

  it("sets the one Lucide stroke width and default size", () => {
    expect(flat).toContain(".lucide { stroke-width: 1.75px; flex: none; }");
    expect(flat).toMatch(/\.lucide:not\(\[data-slot="icon"\]\) \{ width: 16px; height: 16px; \}/);
  });

  it("collapses motion under prefers-reduced-motion", () => {
    const block = flat.slice(flat.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(block).toContain("animation-duration: 1ms !important");
    expect(block).toContain("animation-iteration-count: 1 !important");
    expect(block).toContain("transition-duration: 1ms !important");
  });

  it("draws the global focus-visible outline with the ring token", () => {
    expect(flat).toContain(":focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }");
  });

  it("renders phone inputs at 16px", () => {
    expect(flat).toMatch(/@media \(max-width: 640px\) \{ input:not\([^)]*\), textarea, select \{ font-size: 16px; \} \}/);
  });
});
