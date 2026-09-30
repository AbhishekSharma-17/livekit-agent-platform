import { readFileSync } from "node:fs";
import path from "node:path";

import { formatHex, parse, toGamut } from "culori";
import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";
import { BROWSER_COLORS, THEME_COLOR } from "@/lib/browser-colors";

import { resolveVar, themes } from "../scripts/check-contrast.mjs";

const css = readFileSync(path.resolve(__dirname, "../src/app/globals.css"), "utf8");
const toRgb = toGamut("rgb", "oklch");
const { light, dark } = themes(css) as { light: Record<string, string>; dark: Record<string, string> };

function hexOf(vars: Record<string, string>, token: string): string {
  const parsed = parse(resolveVar(vars, token));
  if (!parsed) throw new Error(`cannot parse ${token}`);
  return formatHex(toRgb(parsed));
}

describe("browser-chrome colour literals", () => {
  it.each([
    ["light", "background", "--background"],
    ["light", "brand", "--brand"],
    ["dark", "background", "--background"],
    ["dark", "brand", "--brand"],
  ] as const)("%s %s mirrors %s", (theme, key, token) => {
    expect(BROWSER_COLORS[theme][key]).toBe(hexOf(theme === "light" ? light : dark, token));
  });

  it("feeds theme-color per colour scheme and the manifest", () => {
    expect(THEME_COLOR).toEqual([
      { media: "(prefers-color-scheme: light)", color: BROWSER_COLORS.light.background },
      { media: "(prefers-color-scheme: dark)", color: BROWSER_COLORS.dark.background },
    ]);
    const m = manifest();
    expect(m.background_color).toBe(BROWSER_COLORS.light.background);
    expect(m.theme_color).toBe(BROWSER_COLORS.light.brand);
    expect(m.start_url).toBe("/console");
  });
});
