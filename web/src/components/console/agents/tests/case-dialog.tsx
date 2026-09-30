"use client";

/**
 * `case-dialog.tsx` (V5-33, `docs/v5/PLAN-V5.md` V5-33): add/edit one test
 * case (`AgentTest`). A dialog, never a side drawer — the case is a handful
 * of longer fields (persona, scenario, expectations, mocks) that don't fit a
 * table row. Saving here only updates the editor's in-memory `config.tests`
 * (via the section's `useFieldArray`); it reaches the api on the editor's
 * own Save, exactly like every other field this editor edits.
 */
import * as React from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { AgentTestForm } from "@/components/console/lib/schemas";

const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const TOOL_NAME_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/;

function slugify(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return slug.slice(0, 64) || "case";
}

interface MockRow {
  key: string;
  tool: string;
  valueText: string;
}

let rowSeq = 0;
function nextRowKey(): string {
  rowSeq += 1;
  return `mock-${rowSeq}`;
}

function emptyCase(): AgentTestForm {
  return { id: "", name: "", persona_instructions: "", scenario: "", expectations: [], mocks: {}, max_turns: 12 };
}

function mocksToRows(mocks: Record<string, unknown>): MockRow[] {
  return Object.entries(mocks).map(([tool, value]) => ({
    key: nextRowKey(),
    tool,
    valueText: typeof value === "string" ? value : JSON.stringify(value),
  }));
}

/** A string is kept as-is (the contract's rule); anything else is parsed as JSON, falling back to the raw string. */
function parseMockValue(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
}

function rowsToMocks(rows: MockRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const row of rows) {
    const tool = row.tool.trim();
    if (tool === "") continue;
    out[tool] = parseMockValue(row.valueText);
  }
  return out;
}

export interface CaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` creates a new case; otherwise edits a copy of it. */
  initial: AgentTestForm | null;
  /** Every other case's id, for the uniqueness check (excludes `initial`'s own id). */
  otherIds: readonly string[];
  onSave: (value: AgentTestForm) => void;
}

export function CaseDialog({ open, onOpenChange, initial, otherIds, onSave }: CaseDialogProps) {
  const [draft, setDraft] = React.useState<AgentTestForm>(() => initial ?? emptyCase());
  const [mockRows, setMockRows] = React.useState<MockRow[]>(() => mocksToRows(initial?.mocks ?? {}));
  const [idTouched, setIdTouched] = React.useState(initial !== null);
  const [attempted, setAttempted] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    setDraft(initial ?? emptyCase());
    setMockRows(mocksToRows(initial?.mocks ?? {}));
    setIdTouched(initial !== null);
    setAttempted(false);
  }, [open, initial]);

  const nameError = attempted && draft.name.trim() === "" ? "Name is required" : undefined;
  const idError =
    draft.id.trim() === ""
      ? attempted
        ? "Case id is required"
        : undefined
      : !ID_PATTERN.test(draft.id)
        ? "Letters, numbers, - or _; must start with a letter or number"
        : otherIds.includes(draft.id)
          ? "Two cases can't share an id"
          : undefined;
  const personaError = attempted && draft.persona_instructions.trim() === "" ? "Describe who is calling" : undefined;
  const mockErrors = mockRows.map((row) =>
    row.tool.trim() !== "" && !TOOL_NAME_PATTERN.test(row.tool.trim()) ? "Not a valid tool name" : undefined,
  );
  const canSave = !nameError && !idError && !personaError && mockErrors.every((e) => !e);

  function handleNameChange(name: string) {
    setDraft((prev) => ({ ...prev, name, id: idTouched ? prev.id : slugify(name) }));
  }

  function handleSave() {
    setAttempted(true);
    if (!canSave) return;
    onSave({ ...draft, id: draft.id.trim(), name: draft.name.trim(), mocks: rowsToMocks(mockRows) });
    onOpenChange(false);
  }

  function setExpectation(index: number, text: string) {
    setDraft((prev) => ({ ...prev, expectations: prev.expectations.map((e, i) => (i === index ? text : e)) }));
  }
  function addExpectation() {
    setDraft((prev) => ({ ...prev, expectations: [...prev.expectations, ""] }));
  }
  function removeExpectation(index: number) {
    setDraft((prev) => ({ ...prev, expectations: prev.expectations.filter((_, i) => i !== index) }));
  }

  function setMockRow(index: number, patch: Partial<MockRow>) {
    setMockRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }
  function addMockRow() {
    setMockRows((prev) => [...prev, { key: nextRowKey(), tool: "", valueText: "" }]);
  }
  function removeMockRow(index: number) {
    setMockRows((prev) => prev.filter((_, i) => i !== index));
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{initial ? "Edit test case" : "Add a test case"}</DialogTitle>
          <DialogDescription>
            A simulated caller with a persona and a goal. The agent plays the real conversation against it; five
            judges score the transcript.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" htmlFor="case-name" error={nameError}>
              <Input
                id="case-name"
                value={draft.name}
                onChange={(event) => handleNameChange(event.target.value)}
                maxLength={120}
              />
            </Field>
            <Field label="Case id" htmlFor="case-id" hint="Used by the run history; letters, numbers, - or _." error={idError}>
              <Input
                id="case-id"
                className="font-mono text-sm"
                value={draft.id}
                onChange={(event) => {
                  setIdTouched(true);
                  setDraft((prev) => ({ ...prev, id: event.target.value.trim() }));
                }}
              />
            </Field>
          </div>
          <Field
            label="Who is calling"
            htmlFor="case-persona"
            hint="Played by an LLM: who they are and how they talk."
            error={personaError}
          >
            <Textarea
              id="case-persona"
              rows={3}
              maxLength={4000}
              value={draft.persona_instructions}
              onChange={(event) => setDraft((prev) => ({ ...prev, persona_instructions: event.target.value }))}
            />
          </Field>
          <Field label="What they want" htmlFor="case-scenario" optional hint="What the caller is trying to get done.">
            <Textarea
              id="case-scenario"
              rows={2}
              maxLength={4000}
              value={draft.scenario}
              onChange={(event) => setDraft((prev) => ({ ...prev, scenario: event.target.value }))}
            />
          </Field>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">Expectations</legend>
            <p className="text-label text-text-secondary">
              What must be true of the agent&apos;s side of the call — the judges check each one.
            </p>
            {draft.expectations.map((expectation, index) => (
              <div key={index} className="flex items-center gap-2">
                <Input
                  aria-label={`Expectation ${index + 1}`}
                  value={expectation}
                  maxLength={500}
                  onChange={(event) => setExpectation(index, event.target.value)}
                />
                <Button type="button" variant="ghost" size="icon" aria-label="Remove expectation" onClick={() => removeExpectation(index)}>
                  <Icon as={Trash2Icon} size="sm" />
                </Button>
              </div>
            ))}
            {draft.expectations.length < 20 ? (
              <Button type="button" variant="secondary" size="sm" className="self-start" onClick={addExpectation}>
                <Icon as={PlusIcon} size="sm" /> Add expectation
              </Button>
            ) : null}
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">Tool mocks</legend>
            <p className="text-label text-text-secondary">
              Optional: make a tool return a fixed result in this case instead of calling out. A plain value is kept
              as text; JSON (e.g. <span className="font-mono">{"{\"ok\": true}"}</span>) is parsed.
            </p>
            {mockRows.map((row, index) => (
              <div key={row.key} className="flex items-start gap-2">
                <Input
                  aria-label={`Mocked tool ${index + 1} name`}
                  placeholder="tool_name"
                  className="w-40 font-mono text-sm"
                  value={row.tool}
                  onChange={(event) => setMockRow(index, { tool: event.target.value })}
                  aria-invalid={mockErrors[index] ? true : undefined}
                />
                <Input
                  aria-label={`Mocked tool ${index + 1} result`}
                  placeholder="Result"
                  className="flex-1 font-mono text-sm"
                  value={row.valueText}
                  onChange={(event) => setMockRow(index, { valueText: event.target.value })}
                />
                <Button type="button" variant="ghost" size="icon" aria-label="Remove mock" onClick={() => removeMockRow(index)}>
                  <Icon as={Trash2Icon} size="sm" />
                </Button>
              </div>
            ))}
            {mockErrors.some(Boolean) ? <p className="text-label text-destructive-text">Not a valid tool name.</p> : null}
            <Button type="button" variant="secondary" size="sm" className="self-start" onClick={addMockRow}>
              <Icon as={PlusIcon} size="sm" /> Add a mocked tool
            </Button>
          </fieldset>

          <Field label="Turn budget" htmlFor="case-max-turns" hint="Caller turns before the conversation is stopped.">
            <Input
              id="case-max-turns"
              type="number"
              inputMode="numeric"
              min={1}
              max={40}
              className="w-24"
              value={draft.max_turns}
              onChange={(event) => {
                const n = Math.trunc(Number(event.target.value));
                setDraft((prev) => ({ ...prev, max_turns: Number.isFinite(n) ? Math.min(40, Math.max(1, n)) : prev.max_turns }));
              }}
            />
          </Field>
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" type="button" onClick={handleSave}>
            {initial ? "Save case" : "Add case"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
