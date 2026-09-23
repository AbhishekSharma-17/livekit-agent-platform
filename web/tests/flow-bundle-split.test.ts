import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * V2-16 acceptance: "canvas bundle is code-split (not in the session bundle)".
 *
 * Walks the **static** import graph (dynamic `import()` is a chunk boundary
 * for Next's bundler; `import type` is erased) from a route's entry files and
 * checks which of our files and which packages it can reach:
 *
 * - `/s/[slug]` (the public session page) reaches nothing of the flow builder
 *   — no `components/console/flow/**`, no `@xyflow/react`, dagre or
 *   jsondiffpatch (R-V2-18's session budget);
 * - `/console/agents/[id]` (the editor) reaches the flow section, but the
 *   canvas and its libraries only through `next/dynamic` / `import()`.
 *
 * (A real `next build` in a scratch copy is the byte-level check; this test
 * keeps the split from regressing on every run.)
 */

const SRC = path.resolve(__dirname, "../src");
const HEAVY = ["@xyflow/react", "@dagrejs/dagre", "jsondiffpatch"];

const STATIC_IMPORT =
  /(?:^|[\n;])\s*(?:import|export)\s+(?!type\s)(?:[\w*{}\s,$]*?\sfrom\s+)?["']([^"']+)["']/g;

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

function resolveLocal(specifier: string, from: string): string | null {
  const base = specifier.startsWith("@/")
    ? path.join(SRC, specifier.slice(2))
    : specifier.startsWith(".")
      ? path.resolve(path.dirname(from), specifier)
      : null;
  if (!base) return null;
  const candidates = [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")];
  return candidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile()) ?? null;
}

function packageName(specifier: string): string {
  const parts = specifier.split("/");
  return specifier.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
}

function staticGraph(entries: string[]): { files: Set<string>; packages: Set<string> } {
  const files = new Set<string>();
  const packages = new Set<string>();
  const queue = entries.map((entry) => path.join(SRC, entry));
  while (queue.length > 0) {
    const file = queue.pop() as string;
    if (files.has(file) || !/\.(ts|tsx)$/.test(file)) continue;
    files.add(file);
    const source = stripComments(readFileSync(file, "utf8"));
    for (const match of source.matchAll(STATIC_IMPORT)) {
      const specifier = match[1];
      const local = resolveLocal(specifier, file);
      if (local) queue.push(local);
      else if (!specifier.startsWith(".") && !specifier.startsWith("@/")) packages.add(packageName(specifier));
    }
  }
  return { files, packages };
}

const rel = (file: string) => path.relative(SRC, file);

describe("flow builder code splitting", () => {
  it("keeps the whole flow builder out of the /s/[slug] session bundle", () => {
    const { files, packages } = staticGraph(["app/layout.tsx", "app/(session)/layout.tsx", "app/(session)/s/[slug]/page.tsx"]);
    expect(files.size).toBeGreaterThan(5);
    expect([...files].map(rel).filter((file) => file.startsWith("components/console/"))).toEqual([]);
    for (const heavy of HEAVY) expect(packages.has(heavy), heavy).toBe(false);
  });

  it("loads the canvas and its libraries lazily from the agent editor", () => {
    const { files, packages } = staticGraph(["app/console/agents/[id]/page.tsx"]);
    const reached = [...files].map(rel);
    expect(reached).toContain("components/console/flow/flow-section.tsx");
    expect(reached).toContain("components/console/flow/extension.ts");
    for (const lazy of ["flow-canvas.tsx", "layout.ts", "node-form.tsx", "edge-form.tsx"]) {
      expect(reached, lazy).not.toContain(`components/console/flow/${lazy}`);
    }
    for (const heavy of HEAVY) expect(packages.has(heavy), heavy).toBe(false);
  });

  it("is a real boundary: the canvas module itself pulls React Flow and dagre", () => {
    const { packages } = staticGraph(["components/console/flow/flow-canvas.tsx"]);
    expect(packages.has("@xyflow/react")).toBe(true);
    expect(packages.has("@dagrejs/dagre")).toBe(true);
  });
});
