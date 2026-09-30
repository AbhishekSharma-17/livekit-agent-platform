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
    expect(result.light.length).toBeGreaterThan(100);
    expect(result.dark.length).toBeGreaterThan(100);
  });

  it("checks the spec section 11 pairs in both themes", () => {
    const result = checkContrast(css) as { light: Row[]; dark: Row[] };
    for (const rows of [result.light, result.dark]) {
      const pairs = rows.map((row) => row.pair);
      for (const role of ["--foreground", "--text-secondary", "--text-tertiary"]) {
        for (const surface of ["--card", "--background", "--muted", "--sidebar", "--sidebar-hover", "--muted-strong"]) {
          expect(pairs).toContain(`${role} on ${surface}`);
        }
      }
      for (const edge of ["--input", "--border-strong", "--ring"]) {
        expect(pairs).toContain(`${edge} on --background`);
        expect(pairs).toContain(`${edge} on --card`);
      }
      expect(pairs).toContain("--brand-foreground on --brand");
      for (const tone of ["success", "warning", "info", "destructive"]) {
        expect(pairs).toContain(`--${tone}-text on --${tone}-subtle`);
        expect(pairs).toContain(`--${tone}-text on --card`);
        expect(pairs).toContain(`--${tone}-solid on --${tone}-subtle`);
        expect(pairs).toContain(`--${tone}-solid on --card`);
        expect(pairs).toContain(`--${tone}-foreground on --${tone}-solid`);
      }
    }
  });

  it("resolves var() alias chains, and success is no longer the accent", () => {
    const vars = extractBlock(css, ":root") as Record<string, string>;
    expect(resolveVar(vars, "--primary")).toBe(vars["--brand"]);
    expect(resolveVar(vars, "--success")).toBe(vars["--success-solid"]);
    expect(resolveVar(vars, "--success")).not.toBe(vars["--brand"]);
    expect(resolveVar(vars, "--danger-text")).toBe(vars["--destructive-text"]);
    expect(resolveVar(vars, "--destructive")).toBe(vars["--destructive-solid"]);
  });

  it("reads the shared `:root, .dark` block for both themes", () => {
    const dark = extractBlock(css, ".dark") as Record<string, string>;
    expect(dark["--background"]).toBeDefined();
    expect(dark["--primary"]).toBe("var(--brand)");
  });

  it("composites tinted fills over both background and card", () => {
    const result = checkContrast(css) as { dark: Row[] };
    const tint = result.dark.filter((row) => row.pair.startsWith("--destructive on --destructive/20"));
    expect(tint.map((row) => row.pair)).toEqual([
      "--destructive on --destructive/20 (over --background)",
      "--destructive on --destructive/20 (over --card)",
    ]);
  });

  it("fails a pair that misses its target", () => {
    const broken = css.replace(/--text-tertiary: oklch\(52% 0\.014 260\);/, "--text-tertiary: oklch(80% 0.01 260);");
    expect(broken).not.toBe(css);
    const result = checkContrast(broken) as { light: Row[] };
    const row = result.light.find((r) => r.pair === "--text-tertiary on --background");
    expect(row?.pass).toBe(false);
  });

  it("catches white text on a light dark-mode status solid", () => {
    const broken = css.replace(/--success-foreground: oklch\(19% 0\.03 150\);/, "--success-foreground: oklch(100% 0 0);");
    expect(broken).not.toBe(css);
    const result = checkContrast(broken) as { dark: Row[] };
    const row = result.dark.find((r) => r.pair === "--success-foreground on --success-solid");
    expect(row?.pass).toBe(false);
  });
});
