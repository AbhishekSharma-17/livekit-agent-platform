"use client";

/**
 * "Test this pattern" (V5-41, ask #280): a bounded, client-side check for a
 * `Pattern` (regex) rule while it's being edited — never a substitute for the
 * api's own check on save (`config_service.py::guardrails_issues`), which is
 * authoritative and runs Python's `re`, not the browser's `RegExp`.
 *
 * Two things keep this "bounded": the sample text is capped (so a single
 * click can't hand a pathological pattern a huge string to backtrack over),
 * and a pattern using Python-only syntax (named groups `(?P<name>...)`,
 * `\Z`, inline flags, …) that fails to compile as a `RegExp` is reported as
 * "can't check this one here", never as "invalid" — the api is the ground
 * truth for whether the pattern itself compiles.
 */
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";

const MAX_SAMPLE_CHARS = 500;

/** An unbounded repeat inside a group: `+`, `*` or `{n,}` — mirrors `config_service.py::_UNBOUNDED_RE`. */
const UNBOUNDED_RE = /[+*]|\{\d*,\}/y;

/**
 * Whether `pattern` repeats a group that itself holds an unbounded repeat
 * (`(a+)+`, `(\w*\s)*`) — such a pattern can backtrack for a very long time
 * on a long text. A soft, client-side mirror of
 * `config_service.py::nested_repeat`; the api's check is what actually
 * blocks a save.
 */
export function hasNestedRepeat(pattern: string): boolean {
  const stack: boolean[] = [];
  let index = 0;
  let inClass = false;
  while (index < pattern.length) {
    const char = pattern[index];
    if (char === "\\") {
      index += 2;
      continue;
    }
    if (inClass) {
      inClass = char !== "]";
    } else if (char === "[") {
      inClass = true;
    } else if (char === "(") {
      stack.push(false);
    } else if (char === ")" && stack.length > 0) {
      const inner = stack.pop() as boolean;
      const next = pattern.slice(index + 1, index + 2);
      if (inner && (next === "+" || next === "*" || next === "{")) return true;
      if (stack.length > 0) stack[stack.length - 1] = stack[stack.length - 1] || inner;
    } else if (stack.length > 0) {
      UNBOUNDED_RE.lastIndex = index;
      if (UNBOUNDED_RE.test(pattern)) stack[stack.length - 1] = true;
    }
    index += 1;
  }
  return false;
}

export type PatternTestResult =
  | { kind: "matched" }
  | { kind: "no-match" }
  | { kind: "cant-check" };

/** Runs `pattern` (as a JS `RegExp`) against `sample`, or reports it can't be checked here. */
export function testPattern(pattern: string, ignoreCase: boolean, sample: string): PatternTestResult {
  try {
    const re = new RegExp(pattern, ignoreCase ? "i" : undefined);
    return re.test(sample.slice(0, MAX_SAMPLE_CHARS)) ? { kind: "matched" } : { kind: "no-match" };
  } catch {
    return { kind: "cant-check" };
  }
}

export function PatternTester({ pattern, ignoreCase }: { pattern: string; ignoreCase: boolean }) {
  const [sample, setSample] = React.useState("");
  const [result, setResult] = React.useState<PatternTestResult | null>(null);
  const slow = React.useMemo(() => pattern.trim() !== "" && hasNestedRepeat(pattern), [pattern]);

  function runTest() {
    setResult(pattern.trim() === "" ? null : testPattern(pattern, ignoreCase, sample));
  }

  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-muted p-3">
      <Field
        label="Test this pattern"
        htmlFor="guardrail-pattern-sample"
        optional
        hint="Type something the caller might say, in this browser only — nothing is sent anywhere."
      >
        <div className="flex gap-2">
          <Input
            id="guardrail-pattern-sample"
            value={sample}
            maxLength={MAX_SAMPLE_CHARS}
            onChange={(event) => {
              setSample(event.target.value);
              setResult(null);
            }}
            placeholder="Try a sample line…"
          />
          <Button type="button" variant="outline" onClick={runTest} disabled={pattern.trim() === ""}>
            Test
          </Button>
        </div>
      </Field>
      {slow ? (
        <p className="text-label text-warning-text">
          This pattern repeats a group that already repeats, which can take very long on long text; the
          server will refuse it on save until it&apos;s simplified.
        </p>
      ) : null}
      {result?.kind === "matched" ? (
        <p className="text-label font-medium text-destructive-text">Matches — this would trip the rule.</p>
      ) : null}
      {result?.kind === "no-match" ? (
        <p className="text-label text-text-secondary">Doesn&apos;t match this sample.</p>
      ) : null}
      {result?.kind === "cant-check" ? (
        <p className="text-label text-text-secondary">
          Can&apos;t check this pattern in the browser — it&apos;s still checked when you save.
        </p>
      ) : null}
    </div>
  );
}
