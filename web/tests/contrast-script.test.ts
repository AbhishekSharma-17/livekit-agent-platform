import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { checkContrast, extractBlock, resolveVar } from "../scripts/check-contrast.mjs";

interface Row {
  pair: string;
  ratio: number;
  min: number;
  pass: boolean;
}

const css = readFileSync(path.resolve(__dirname, "../src/app/globals.css"), "utf8");

describe("scripts/check-contrast.mjs", () => {
  it("passes every WCAG pair in both themes for the shipped tokens", () => {
    const result = checkContrast(css) as { light: Row[]; dark: Row[] };
    const failures = [...result.light, ...result.dark].filter((row) => !row.pass);
    expect(failures).toEqual([]);
    expect(result.light.length).toBeGreaterThan(20);
  });

  it("resolves var() alias chains", () => {
    const vars = extractBlock(css, ":root") as Record<string, string>;
    expect(resolveVar(vars, "--success")).toBe(vars["--brand"]);
    expect(resolveVar(vars, "--ring")).toBe(vars["--brand"]);
  });

  it("composites translucent tokens over both background and card", () => {
    const result = checkContrast(css) as { dark: Row[] };
    const soft = result.dark.filter((row) => row.pair.startsWith("--brand-text on --brand-soft"));
    expect(soft.map((row) => row.pair)).toEqual([
      "--brand-text on --brand-soft (over --background)",
      "--brand-text on --brand-soft (over --card)",
    ]);
  });

  it("fails a pair that misses its target", () => {
    const broken = css.replace(/--muted-foreground: oklch\(0\.47 0\.015 250\);/, "--muted-foreground: oklch(0.8 0.01 250);");
    const result = checkContrast(broken) as { light: Row[] };
    const row = result.light.find((r) => r.pair === "--muted-foreground on --background");
    expect(row?.pass).toBe(false);
  });
});
