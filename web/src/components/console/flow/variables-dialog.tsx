"use client";

import * as React from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
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
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { VariableSpec } from "@/contracts/lkap-contracts";

import { VARIABLE_NAME_PATTERN } from "./flow-model";

const TYPES: NonNullable<VariableSpec["type"]>[] = ["string", "number", "boolean", "enum", "date", "phone", "email"];

/**
 * The flow's variables (V2-16): what steps collect on exit (`extract`) and
 * what text fields reference as `{{ name }}` (inserted with `@`). Edits are
 * local until "Done"; renames carry over to every step that collects the
 * variable (placeholders in text are the author's to update).
 */
export function VariablesDialog({
  open,
  onOpenChange,
  variables,
  onApply,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  variables: readonly VariableSpec[];
  onApply: (next: VariableSpec[], renames: Record<string, string>) => void;
}) {
  const [rows, setRows] = React.useState<(VariableSpec & { original?: string })[]>([]);
  React.useEffect(() => {
    if (open) setRows(variables.map((variable) => ({ ...variable, original: variable.name })));
  }, [open, variables]);

  const names = rows.map((row) => row.name);
  const problems = rows.map((row, index) => {
    if (!VARIABLE_NAME_PATTERN.test(row.name)) return "Use lowercase letters, digits and _, starting with a letter.";
    if (names.indexOf(row.name) !== index) return "Already used.";
    if (row.type === "enum" && !(row.options ?? []).length) return "List the options.";
    return null;
  });
  const invalid = problems.some(Boolean);

  const update = (index: number, patch: Partial<VariableSpec>) =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Flow variables</DialogTitle>
          <DialogDescription>
            Values the agent collects during the call. Steps fill them on exit; any text field can use them with
            @. They end up on the session and in the session.ended webhook.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="gap-3">
          {rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">No variables yet.</p>
          ) : (
            rows.map((row, index) => {
              const base = `flow-variable-${index}`;
              return (
                <div
                  key={index}
                  data-variable-row={row.name}
                  className="grid grid-cols-1 gap-2 rounded-md border border-border p-3 md:grid-cols-[minmax(0,1fr)_8rem_auto]"
                >
                  <div className="flex flex-col gap-1">
                    <label htmlFor={`${base}-name`} className="text-xs font-medium">
                      Name
                    </label>
                    <Input
                      id={`${base}-name`}
                      value={row.name}
                      aria-invalid={problems[index] ? true : undefined}
                      aria-describedby={problems[index] ? `${base}-error` : undefined}
                      onChange={(event) => update(index, { name: event.target.value.trim() })}
                    />
                    {problems[index] ? (
                      <p id={`${base}-error`} className="text-xs text-danger-text">
                        {problems[index]}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-col gap-1">
                    <label htmlFor={`${base}-type`} className="text-xs font-medium">
                      Type
                    </label>
                    <Select
                      value={row.type ?? "string"}
                      onValueChange={(next) => update(index, { type: next as VariableSpec["type"] })}
                    >
                      <SelectTrigger id={`${base}-type`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TYPES.map((type) => (
                          <SelectItem key={type} value={type}>
                            {type}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-end justify-between gap-3 md:justify-end">
                    <label className="flex items-center gap-2 text-xs font-medium">
                      <Switch
                        checked={row.required ?? false}
                        onCheckedChange={(checked) => update(index, { required: checked })}
                        aria-label={`${row.name || "Variable"} is required`}
                      />
                      Required
                    </label>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Remove ${row.name || "variable"}`}
                      onClick={() => setRows((current) => current.filter((_row, i) => i !== index))}
                    >
                      <Icon as={Trash2Icon} />
                    </Button>
                  </div>
                  <div className="flex flex-col gap-1 md:col-span-3">
                    <label htmlFor={`${base}-description`} className="text-xs font-medium">
                      Description
                    </label>
                    <Input
                      id={`${base}-description`}
                      value={row.description ?? ""}
                      placeholder="What the value is, for the extraction model"
                      onChange={(event) => update(index, { description: event.target.value })}
                    />
                  </div>
                  {row.type === "enum" ? (
                    <div className="flex flex-col gap-1 md:col-span-3">
                      <label htmlFor={`${base}-options`} className="text-xs font-medium">
                        Options
                      </label>
                      <Input
                        id={`${base}-options`}
                        value={(row.options ?? []).join(", ")}
                        placeholder="Comma-separated"
                        onChange={(event) =>
                          update(index, {
                            options: event.target.value
                              .split(",")
                              .map((item) => item.trim())
                              .filter(Boolean),
                          })
                        }
                      />
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </DialogBody>
        <DialogFooter className="sm:justify-between">
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              setRows((current) => [
                ...current,
                { name: uniqueName(current.map((row) => row.name)), type: "string", description: "", required: false },
              ])
            }
          >
            <Icon as={PlusIcon} />
            Add variable
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={invalid}
              onClick={() => {
                const renames: Record<string, string> = {};
                for (const row of rows) if (row.original && row.original !== row.name) renames[row.original] = row.name;
                onApply(
                  rows.map((row) => ({
                    name: row.name,
                    type: row.type,
                    description: row.description ?? "",
                    required: row.required ?? false,
                    options: row.type === "enum" ? (row.options ?? []) : null,
                  })),
                  renames,
                );
                onOpenChange(false);
              }}
            >
              Done
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function uniqueName(taken: readonly string[]): string {
  for (let n = 1; ; n += 1) {
    const name = n === 1 ? "variable" : `variable_${n}`;
    if (!taken.includes(name)) return name;
  }
}
