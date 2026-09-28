"use client";

import * as React from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { InsertValueMenu } from "@/components/console/tools/insert-value-menu";
import { useInsertableField } from "@/components/console/tools/use-insertable-field";
import {
  MAX_PINNED_ARGUMENTS,
  TOOL_ARGUMENT_PATTERN,
  pinnedArgumentIssues,
} from "@/components/console/tools/tool-context";

export type PinnedValue = string | number | boolean | null;
type PinnedType = "string" | "number" | "boolean" | "null";

function typeOf(value: PinnedValue): PinnedType {
  if (value === null) return "null";
  return typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : "string";
}

function defaultForType(type: PinnedType): PinnedValue {
  return type === "string" ? "" : type === "number" ? 0 : type === "boolean" ? false : null;
}

/** Whether `pinned` has a name Save should refuse — for the caller's Save gate. */
export function pinnedArgumentsHaveIssues(pinned: Record<string, PinnedValue>): boolean {
  if (Object.keys(pinned).length > MAX_PINNED_ARGUMENTS) return true;
  return Object.keys(pinned).some((name) => !TOOL_ARGUMENT_PATTERN.test(name));
}

/**
 * "Fixed values" (`pinned_arguments`, D-V6-22): arguments an admin sets once, hidden from the
 * model entirely — an app action or MCP tool only (`ProviderToolDefinition`,
 * `ToolContextSpec`; ask #34). A string value may use `{{ ctx.* }}` / `{{ var.* }}`, rendered
 * the same as any other JSON value the api sends (no percent-encoding — this isn't a url).
 */
export function PinnedArgumentsEditor({
  values,
  onChange,
  variableNames,
}: {
  values: Record<string, PinnedValue>;
  onChange: (next: Record<string, PinnedValue>) => void;
  variableNames: readonly string[];
}) {
  const entries = Object.entries(values);
  const atCap = entries.length >= MAX_PINNED_ARGUMENTS;
  const issues = pinnedArgumentIssues(values);
  const [draftName, setDraftName] = React.useState("");

  function addRow() {
    const name = draftName.trim();
    if (!name || atCap || name in values) return;
    onChange({ ...values, [name]: "" });
    setDraftName("");
  }

  function renameRow(oldName: string, newName: string) {
    if (newName === oldName || newName in values) return;
    const next: Record<string, PinnedValue> = {};
    for (const [name, value] of Object.entries(values)) next[name === oldName ? newName : name] = value;
    onChange(next);
  }

  function setValue(name: string, value: PinnedValue) {
    onChange({ ...values, [name]: value });
  }

  function setType(name: string, type: PinnedType) {
    setValue(name, defaultForType(type));
  }

  function removeRow(name: string) {
    const next = { ...values };
    delete next[name];
    onChange(next);
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium text-foreground">Fixed values</h3>
        <p className="text-[0.8125rem] text-pretty text-muted-foreground">
          Set once here and hidden from the model — it never sees these arguments or chooses them.
        </p>
      </div>
      {entries.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Argument</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Value</TableHead>
              <TableHead className="sr-only">Remove</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map(([name, value]) => (
              <PinnedRow
                key={name}
                name={name}
                value={value}
                variableNames={variableNames}
                onRename={(next) => renameRow(name, next)}
                onValueChange={(next) => setValue(name, next)}
                onTypeChange={(type) => setType(name, type)}
                onRemove={() => removeRow(name)}
              />
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-[0.8125rem] text-muted-foreground">No fixed values yet.</p>
      )}
      <div className="flex gap-2">
        <Input
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          placeholder="argument_name"
          aria-label="New fixed argument name"
          className="w-48 font-mono text-sm"
          disabled={atCap}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addRow();
            }
          }}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={addRow}
          disabled={atCap || draftName.trim() === ""}
          aria-label="Add a fixed value"
        >
          <PlusIcon className="size-3.5" aria-hidden="true" />
          Add
        </Button>
      </div>
      {atCap ? <p className="text-[0.8125rem] text-warning-text">At most {MAX_PINNED_ARGUMENTS} fixed values.</p> : null}
      {issues.length > 0 ? (
        <ul className="flex flex-col gap-0.5">
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

function PinnedRow({
  name,
  value,
  variableNames,
  onRename,
  onValueChange,
  onTypeChange,
  onRemove,
}: {
  name: string;
  value: PinnedValue;
  variableNames: readonly string[];
  onRename: (next: string) => void;
  onValueChange: (next: PinnedValue) => void;
  onTypeChange: (type: PinnedType) => void;
  onRemove: () => void;
}) {
  const type = typeOf(value);
  const stringValue = typeof value === "string" ? value : "";
  const field = useInsertableField<HTMLInputElement>(stringValue, (next) => onValueChange(next));
  const nameInvalid = !TOOL_ARGUMENT_PATTERN.test(name);

  return (
    <TableRow>
      <TableCell>
        <Input
          defaultValue={name}
          onBlur={(e) => onRename(e.target.value.trim())}
          aria-label={`Fixed argument name (${name})`}
          aria-invalid={nameInvalid}
          className="w-36 font-mono text-xs"
        />
      </TableCell>
      <TableCell>
        <Select value={type} onValueChange={(v) => onTypeChange(v as PinnedType)}>
          <SelectTrigger className="w-28" aria-label={`${name} — type`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="string">Text</SelectItem>
            <SelectItem value="number">Number</SelectItem>
            <SelectItem value="boolean">Yes / no</SelectItem>
            <SelectItem value="null">Empty</SelectItem>
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        {type === "string" ? (
          <div className="flex items-center gap-1.5">
            <Input
              ref={field.ref}
              value={stringValue}
              onChange={(e) => onValueChange(e.target.value)}
              onSelect={field.trackCaret}
              onClick={field.trackCaret}
              onKeyUp={field.trackCaret}
              aria-label={`${name} — value`}
              className="w-40 font-mono text-xs"
            />
            <InsertValueMenu variableNames={variableNames} onInsert={field.insert} label="Insert" />
          </div>
        ) : type === "number" ? (
          <Input
            type="number"
            value={typeof value === "number" ? value : 0}
            onChange={(e) => onValueChange(Number(e.target.value))}
            aria-label={`${name} — value`}
            className="w-32"
          />
        ) : type === "boolean" ? (
          <Switch checked={value === true} onCheckedChange={(checked) => onValueChange(checked)} aria-label={`${name} — value`} />
        ) : (
          <span className="text-xs text-muted-foreground">Empty</span>
        )}
      </TableCell>
      <TableCell>
        <Button type="button" variant="ghost" size="icon" onClick={onRemove} aria-label={`Remove ${name}`}>
          <Trash2Icon className="size-4" aria-hidden="true" />
        </Button>
      </TableCell>
    </TableRow>
  );
}
