"use client";

import * as React from "react";

import { fieldIds } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { MAX_CONFIRM_READBACK, readbackIssues } from "@/components/console/tools/tool-context";

/** Whether `values` has a name Save should refuse — for the caller's Save gate. */
export function confirmReadbackHasIssues(
  values: readonly string[],
  argumentNames: readonly string[] | null,
  pinnedNames: readonly string[] = [],
): boolean {
  if (values.length > MAX_CONFIRM_READBACK) return true;
  return readbackIssues(values, argumentNames ? new Set(argumentNames) : null, new Set(pinnedNames)).length > 0;
}

/**
 * "Read back before calling" (`confirm_readback`, D-V6-22): argument names the model must say
 * back to the caller before the tool runs (it refuses until the next call sets
 * `confirmed=true`). A picker over the tool's own arguments when its schema is known
 * (`argumentNames`); free text otherwise (an MCP tool with no cached snapshot yet).
 */
export function ReadbackField({
  uid,
  values,
  onChange,
  argumentNames,
  pinnedNames = [],
}: {
  uid: string;
  values: string[];
  onChange: (next: string[]) => void;
  /** `null` when the tool's argument schema isn't known here (checkboxes need it; falls back to free text). */
  argumentNames: readonly string[] | null;
  /** Excluded from the picker — a pinned argument is hidden from the model, so it can't read one back. */
  pinnedNames?: readonly string[];
}) {
  const htmlFor = `${uid}-confirm-readback`;
  const ids = fieldIds(htmlFor);
  const [draftName, setDraftName] = React.useState("");
  const atCap = values.length >= MAX_CONFIRM_READBACK;
  const issues = readbackIssues(values, argumentNames ? new Set(argumentNames) : null, new Set(pinnedNames));
  const pickable = (argumentNames ?? []).filter((name) => !pinnedNames.includes(name));

  function toggle(name: string, checked: boolean) {
    if (checked) {
      if (atCap || values.includes(name)) return;
      onChange([...values, name]);
    } else {
      onChange(values.filter((v) => v !== name));
    }
  }

  function add(name: string) {
    const trimmed = name.trim();
    if (!trimmed || values.includes(trimmed) || atCap) return;
    onChange([...values, trimmed]);
    setDraftName("");
  }

  return (
    <div data-slot="field" className="flex flex-col gap-1.5">
      <Label htmlFor={htmlFor}>Read back before calling</Label>
      <p id={ids.hint} className="text-label leading-[1.125rem] text-pretty text-text-secondary">
        The model says these values back to the caller and gets confirmation before the tool runs.
      </p>
      {pickable.length > 0 ? (
        <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
          <legend className="sr-only">Arguments to read back</legend>
          {pickable.map((name) => (
            <label key={name} className="flex items-center gap-2 text-body">
              <input
                type="checkbox"
                className="size-3.5 rounded-sm border-input"
                checked={values.includes(name)}
                disabled={!values.includes(name) && atCap}
                onChange={(e) => toggle(name, e.target.checked)}
              />
              <span className="font-mono text-caption">{name}</span>
            </label>
          ))}
        </fieldset>
      ) : (
        <div className="flex flex-col gap-2">
          {values.length > 0 ? (
            <ul className="flex flex-wrap gap-1.5">
              {values.map((name) => (
                <li key={name} className="flex items-center gap-1 rounded-pill border border-border bg-muted/50 px-2.5 py-0.5 font-mono text-caption">
                  {name}
                  <button
                    type="button"
                    onClick={() => onChange(values.filter((v) => v !== name))}
                    aria-label={`Remove ${name}`}
                    className="text-text-secondary hover:text-foreground"
                  >
                    ×
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
              placeholder="email"
              aria-describedby={ids.hint}
              className="font-mono text-body"
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
              variant="secondary"
              onClick={() => add(draftName)}
              disabled={atCap || draftName.trim() === ""}
              aria-label="Add an argument to read back"
            >
              Add
            </Button>
          </div>
        </div>
      )}
      {atCap ? <p className="text-label text-warning-text">At most {MAX_CONFIRM_READBACK} arguments to read back.</p> : null}
      {issues.length > 0 ? (
        <ul id={ids.error} className="flex flex-col gap-0.5">
          {issues.map((issue) => (
            <li key={issue} className="text-label text-destructive-text">
              {issue}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
