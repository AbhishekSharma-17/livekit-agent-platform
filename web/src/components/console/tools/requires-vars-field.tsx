"use client";

import * as React from "react";
import { XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fieldIds } from "@/components/shared/field";
import { Label } from "@/components/ui/label";
import { VARIABLE_NAME_PATTERN, MAX_REQUIRES_VARS, requiresVarsIssues, variableLabel } from "@/components/console/tools/tool-context";

/** Whether `values` has a name Save should refuse — for the caller's Save gate. */
export function requiresVarsHaveIssues(values: readonly string[]): boolean {
  return values.length > MAX_REQUIRES_VARS || requiresVarsIssues(values).length > 0;
}

/**
 * "Needs these values first" (`requires_vars`, D-V6-22): a chip list of variable names the
 * tool refuses to run without — the refusal names them, so the model asks. A composite
 * control (not `Field`'s single-child clone), so `fieldIds(htmlFor)` is wired by hand
 * (`field.tsx`'s own rule for a layout wrapper).
 */
export function RequiresVarsField({
  uid,
  values,
  onChange,
  knownVariables,
}: {
  uid: string;
  values: string[];
  onChange: (next: string[]) => void;
  /** The agent's flow variables, offered as quick-add chips below the input. */
  knownVariables: readonly string[];
}) {
  const htmlFor = `${uid}-requires-vars`;
  const ids = fieldIds(htmlFor);
  const [draftName, setDraftName] = React.useState("");
  const atCap = values.length >= MAX_REQUIRES_VARS;
  const issues = requiresVarsIssues(values);
  const suggestions = knownVariables.filter((name) => !values.includes(name));

  function add(name: string) {
    const trimmed = name.trim();
    if (!trimmed || values.includes(trimmed) || atCap) return;
    onChange([...values, trimmed]);
    setDraftName("");
  }

  function remove(name: string) {
    onChange(values.filter((v) => v !== name));
  }

  return (
    <div data-slot="field" className="flex flex-col gap-1.5">
      <Label htmlFor={htmlFor}>Needs these values first</Label>
      <p id={ids.hint} className="text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">
        The tool asks for these before it runs, instead of calling out with them missing.
      </p>
      {values.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5">
          {values.map((name) => (
            <li key={name} className="flex items-center gap-1 rounded-full border border-border bg-muted/50 py-0.5 pr-1 pl-2.5 text-xs">
              <span className="font-mono">{name}</span>
              <span className="sr-only"> — {variableLabel(name)}</span>
              <button
                type="button"
                onClick={() => remove(name)}
                aria-label={`Remove ${name}`}
                className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <XIcon className="size-3" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <Input
          id={htmlFor}
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          placeholder="policy_no"
          aria-describedby={ids.hint}
          className="font-mono text-sm"
          disabled={atCap}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(draftName);
            }
          }}
        />
        <Button
          type="button"
          variant="outline"
          onClick={() => add(draftName)}
          disabled={atCap || draftName.trim() === ""}
          aria-label="Add a required variable"
        >
          Add
        </Button>
      </div>
      {suggestions.length > 0 && !atCap ? (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => add(name)}
              className="rounded-full border border-dashed border-border px-2.5 py-0.5 font-mono text-xs text-muted-foreground hover:border-foreground hover:text-foreground"
            >
              + {name}
            </button>
          ))}
        </div>
      ) : null}
      {atCap ? <p className="text-[0.8125rem] text-warning-text">At most {MAX_REQUIRES_VARS} required variables.</p> : null}
      {draftName.trim() !== "" && !VARIABLE_NAME_PATTERN.test(draftName.trim()) ? (
        <p className="text-[0.8125rem] text-danger-text">A variable name is lower case letters, digits and _.</p>
      ) : null}
      {issues.length > 0 ? (
        <ul id={ids.error} className="flex flex-col gap-0.5">
          {issues.map((issue) => (
            <li key={issue} className="text-[0.8125rem] text-danger-text">
              {issue}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
