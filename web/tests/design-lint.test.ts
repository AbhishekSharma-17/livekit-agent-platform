import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  TOKEN_FILE,
  blankComments,
  bridgeColourNames,
  buildMatchers,
  lintCssComments,
  lintSource,
  parseAllowlist,
  runDesignLint,
  shrinkAllowlist,
  sourceFiles,
} from "../scripts/design-lint.mjs";

interface Violation {
  rule: string;
  line: number;
  col: number;
  match: string;
}

const root = path.resolve(__dirname, "..");
const tokenCss = readFileSync(path.join(root, TOKEN_FILE), "utf8");
const allowlistText = readFileSync(path.join(root, "scripts/design-lint-allowlist.txt"), "utf8");
const matchers = buildMatchers(bridgeColourNames(tokenCss));

function rules(relPath: string, text: string): string[] {
  return (lintSource(relPath, text, matchers) as Violation[]).map((v) => v.rule);
}

describe("scripts/design-lint.mjs on the codebase", () => {
  it("passes with the checked-in allowlist", () => {
    const files = Object.fromEntries(
      (sourceFiles(root) as string[]).map((rel) => [rel, readFileSync(path.join(root, rel), "utf8")]),
    );
    const result = runDesignLint({ files, tokenCss, allowlistText }) as { errors: string[] };
    expect(result.errors).toEqual([]);
  });

  it("keeps the allowlist free of parse errors and unknown rules", () => {
    expect((parseAllowlist(allowlistText) as { errors: string[] }).errors).toEqual([]);
  });

  it("allows only deliberate, commented exceptions", () => {
    const entries = allowlistText.split("\n").filter((line) => /^\s*(allow|exempt)\s/.test(line));
    for (const line of entries) {
      if (/^\s*allow\s/.test(line)) expect(line, line).toMatch(/#\s*keep:\s*\S/);
      // exempt <rule> <prefix> <reason…>: the reason is required.
      else expect(line.replace(/#.*$/, "").trim().split(/\s+/).length, line).toBeGreaterThan(3);
    }
    const allow = (parseAllowlist(allowlistText) as { allow: Map<string, unknown> }).allow;
    expect(allow.size).toBeLessThanOrEqual(2);
  });
});

describe("design-lint rules", () => {
  it.each([
    ["colour-literal", 'const c = "#1f2937";'],
    ["colour-literal", "style={{ color: 'rgb(0 0 0)' }}"],
    ["colour-literal", 'className="bg-[oklch(0.5_0.1_20)]"'],
    ["raw-shadow", "style={{ boxShadow: '0 1px 2px black' }}"],
    ["raw-shadow", 'className="shadow-[0_2px_4px_var(--border)]"'],
    ["native-select", "<select value={v}>"],
    ["browser-dialog", "if (window.confirm('Sure?')) go();"],
    ["browser-dialog", "alert('done');"],
    ["stroke-width", "<XIcon strokeWidth={2} />"],
    ["stroke-width", "<XIcon absoluteStrokeWidth />"],
    ["sparkle-icon", 'import { SparklesIcon } from "lucide-react";'],
    ["sparkle-icon", "<WandSparkles />"],
    ["palette-class", 'className="bg-slate-500"'],
    ["palette-class", 'className="hover:text-white"'],
    ["opacity-colour", 'className="bg-muted/50"'],
    ["opacity-colour", 'className="dark:ring-destructive/40"'],
    ["opacity-colour", 'className="bg-muted-foreground/[0.2]"'],
    ["legacy-token", 'className="text-muted-foreground"'],
    ["legacy-token", 'className="data-[state=open]:bg-accent"'],
    ["legacy-token", 'className="bg-brand-soft border-brand-line text-brand-text"'],
    ["legacy-token", 'className="text-danger-text bg-danger-soft"'],
    ["legacy-token", 'className="accent-primary"'],
    ["legacy-token", 'className="shadow-md rounded-xl"'],
    ["legacy-token", 'className="focus-visible:after:rounded-md"'],
    ["legacy-token", 'className="transition-colors ease-out"'],
    ["legacy-token", 'const tone = "var(--danger)";'],
    ["legacy-token", ".x { box-shadow: var(--shadow-sm); }"],
    // the house copy style has no em dash (UI-R2b)
    ["em-dash", "<p>Save your changes first — a run plays the saved version.</p>"],
    ["em-dash", 'toast.error("Couldn\'t save — try again");'],
    ["em-dash", "const title = `${name} — live session`;"],
    ["em-dash", 'aria-label={"Binding 1 \\u2014 card"}'],
    ["em-dash", "<span>A &mdash; B</span>"],
    ["em-dash", "<span>A &#8212; B</span>"],
  ])("flags %s in `%s`", (rule, source) => {
    expect(rules("src/components/x.tsx", source)).toContain(rule);
  });

  it.each([
    'href="#main-content"',
    "// see issue #215 and #add",
    "/* #fff in a comment */",
    'className="text-sm/6 bg-muted text-text-secondary w-1/2"',
    // spec names that share a stem with a legacy alias
    'className="text-destructive-text bg-destructive-subtle bg-info-subtle text-success-text bg-warning-solid"',
    'className="text-brand bg-brand-subtle border-brand-border accent-brand shadow-overlay rounded-dialog"',
    'className="rounded ease-entrance bg-sidebar-hover"',
    // session-only survivors (docs/ui/AUDIT.md section 4)
    'className="bg-stage text-stage-foreground rounded-2xl ease-in-out"',
    "toast.alert(message)",
    "function alert(x) {}",
    "const selected = items.filter(Boolean);",
    'className="rounded-lg shadow-raised"',
    'const url = "https://example.com/#cafe";',
    // em dashes in comments are not copy; hyphens and middle dots are fine
    "// the editor rail — see DESIGN-SYSTEM.md",
    "/* one place — the token file */",
    "{/* JSX comment — not rendered */}",
    'const meta = "12 sessions · $0.40";',
    'const hint = "A well-known, low-latency model.";',
  ])("does not flag `%s`", (source) => {
    expect(rules("src/components/x.tsx", source)).toEqual([]);
  });

  it("lets the token file hold colours and shadows", () => {
    expect(rules(TOKEN_FILE, ":root { --x: oklch(50% 0.1 20); --y: 0 1px 2px oklch(0% 0 0 / 0.3); }")).toEqual([]);
    expect(rules("src/app/other.css", ".x { color: #fff; box-shadow: 0 1px 2px #000; }")).toEqual([
      "colour-literal",
      "colour-literal",
      "raw-shadow",
    ]);
  });

  it("blanks comments but keeps strings, lines and columns", () => {
    const text = 'a // b\nconst s = "http://x"; /* c\n d */ e';
    const out = blankComments(text) as string;
    expect(out.length).toBe(text.length);
    expect(out.split("\n")).toHaveLength(3);
    expect(out).toContain('"http://x"');
    expect(out).not.toContain("b");
    expect(out.trimEnd().endsWith("e")).toBe(true);
  });
});

describe("CSS comment safety", () => {
  it("flags a glob that closes a comment early", () => {
    const found = lintCssComments("/* matches src/**/*.ts files */\n.a { color: red; }") as Violation[];
    expect(found.map((v) => v.match)).toEqual(["/* inside a comment", "stray */"]);
    expect(found[1].line).toBe(1);
  });

  it("flags an unterminated comment", () => {
    expect((lintCssComments(".a {}\n/* open") as Violation[]).map((v) => v.match)).toEqual(["unterminated /*"]);
  });

  it("passes well-formed comments and the token file", () => {
    expect(lintCssComments("/* one */ .a { content: '*/'; } /* two */")).toEqual([]);
    expect(lintCssComments(tokenCss)).toEqual([]);
  });
});

describe("allowlist ratchet", () => {
  const files = { "src/components/a.tsx": 'className="bg-muted/50 bg-card/40"' };
  const lint = (text: string) =>
    runDesignLint({ files, tokenCss, allowlistText: text }) as {
      errors: string[];
      counts: Map<string, number>;
      allowed: Record<string, { files: number; violations: number }>;
    };

  it("fails a file that is not listed", () => {
    const { errors } = lint("");
    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain("src/components/a.tsx: 2 opacity-colour violation(s), 0 allowed");
    expect(errors[0]).toContain("src/components/a.tsx:1:12  bg-muted/50");
  });

  it("passes at the allowance and reports it", () => {
    const result = lint("allow opacity-colour 2 src/components/a.tsx");
    expect(result.errors).toEqual([]);
    expect(result.allowed["opacity-colour"]).toEqual({ files: 1, violations: 2 });
  });

  it("fails when a file improves but its line is not lowered", () => {
    const { errors } = lint("allow opacity-colour 3 src/components/a.tsx");
    expect(errors).toEqual([
      expect.stringContaining("src/components/a.tsx now has 2 opacity-colour violation(s); lower the allowance to 2"),
    ]);
  });

  it("exempts the vendored ui primitives from legacy-token only", () => {
    const { exempt } = parseAllowlist(allowlistText) as { exempt: { rule: string; prefix: string }[] };
    expect(exempt).toContainEqual(expect.objectContaining({ rule: "legacy-token", prefix: "src/components/ui/" }));
    const screen = { "src/components/console/x.tsx": 'className="text-muted-foreground"' };
    const vendored = { "src/components/ui/x.tsx": 'className="text-muted-foreground"' };
    const errorsFor = (files: Record<string, string>) =>
      (runDesignLint({ files, tokenCss, allowlistText }) as { errors: string[] }).errors.filter((e) => e.includes("/x.tsx"));
    expect(errorsFor(screen)).toEqual([expect.stringContaining("1 legacy-token violation(s), 0 allowed")]);
    expect(errorsFor(vendored)).toEqual([]);
  });

  it("honours exempt prefixes per rule", () => {
    expect(lint("exempt opacity-colour src/components/").errors).toEqual([]);
    expect(lint("exempt palette-class src/components/").errors).toHaveLength(1);
  });

  it("fails an em dash in copy unless the file is exempt or holds a deliberate allowance", () => {
    const copy = { "src/components/console/x.tsx": '<p>Save first — then run.</p>\nconst t = "a — b";' };
    const run = (text: string) => (runDesignLint({ files: copy, tokenCss, allowlistText: text }) as { errors: string[] }).errors;
    expect(run("")).toEqual([expect.stringContaining("2 em-dash violation(s), 0 allowed")]);
    expect(run("allow em-dash 2 src/components/console/x.tsx  # keep: a quoted product name")).toEqual([]);
    expect(run("exempt em-dash src/components/console/  vendored copy")).toEqual([]);
    // the checked-in allowlist keeps the leave-alone trees out of the copy rule
    const leaveAlone = { "src/panels/x.tsx": "<p>A — B</p>", "src/contracts/x.ts": 'const a = "A — B";' };
    const errors = (runDesignLint({ files: leaveAlone, tokenCss, allowlistText }) as { errors: string[] }).errors;
    expect(errors.filter((e) => e.includes("/x.ts"))).toEqual([]);
  });

  it("rejects unknown rules and malformed lines", () => {
    const { errors } = lint("allow opacity-colour 2 src/components/a.tsx\nallow made-up 1 src/x.tsx\nnonsense");
    expect(errors.some((e) => e.includes('unknown rule "made-up"'))).toBe(true);
    expect(errors.some((e) => e.includes('cannot parse "nonsense"'))).toBe(true);
  });

  it("--shrink lowers counts and drops cleared lines, never raising one", () => {
    const counts = new Map([
      ["opacity-colour src/a.tsx", 1],
      ["palette-class src/b.tsx", 5],
    ]);
    const text = [
      "# header",
      "allow opacity-colour   3  src/a.tsx",
      "allow palette-class    2  src/b.tsx",
      "allow raw-shadow       1  src/c.tsx",
    ].join("\n");
    expect(shrinkAllowlist(text, counts)).toBe(
      ["# header", "allow opacity-colour   1  src/a.tsx", "allow palette-class    2  src/b.tsx"].join("\n"),
    );
  });
});
