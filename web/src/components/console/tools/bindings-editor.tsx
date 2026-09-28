"use client";

import * as React from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ToolBinding } from "@/contracts/lkap-contracts";
import {
  MAX_BINDINGS,
  VARIABLE_NAME_PATTERN,
  bindingPathIssue,
  bindingTargetToString,
  parseBindingTarget,
  type BindingKind,
} from "@/components/console/tools/tool-context";

const KIND_LABEL: Record<BindingKind, string> = {
  details: "A card's field",
  table: "A table",
  checklist: "A checklist item",
  status: "The status",
  note: "A note",
  var: "A variable",
};
const KINDS: BindingKind[] = ["details", "table", "checklist", "status", "note", "var"];

interface BindingRow {
  path: string;
  kind: BindingKind;
  blockId: string;
  key: string;
  /** Set only when the stored `to` didn't parse (foreign data) — kept and resaved verbatim unless the row is touched. */
  rawTo?: string;
}

/**
 * Reads a stored `to` back into a row, leniently: a *complete* target parses through
 * `parseBindingTarget` (also catching the shape a foreign MCP client wrote); an
 * *incomplete* one an editor itself just produced (`bindingTargetToString` of a row whose
 * key or block isn't filled in yet, e.g. `"details:card."`) is read back by prefix instead,
 * so a still-being-filled-in row keeps its chosen kind across a re-render rather than
 * bouncing to "status" the moment its `to` string briefly fails to parse. Only a `to` that
 * matches neither is kept verbatim as `rawTo`.
 */
function rowFromBinding(binding: ToolBinding): BindingRow {
  const path = binding.path ?? "";
  const to = binding.to ?? "";
  const target = parseBindingTarget(to);
  if (target) return { path, kind: target.kind, blockId: target.blockId ?? "", key: target.key ?? "" };
  if (to === "status" || to === "") return { path, kind: "status", blockId: "", key: "" };
  if (to === "note") return { path, kind: "note", blockId: "", key: "" };
  if (to.startsWith("details:")) {
    const rest = to.slice("details:".length);
    const dot = rest.lastIndexOf(".");
    return dot === -1
      ? { path, kind: "details", blockId: rest, key: "" }
      : { path, kind: "details", blockId: rest.slice(0, dot), key: rest.slice(dot + 1) };
  }
  if (to.startsWith("table:")) return { path, kind: "table", blockId: to.slice("table:".length), key: "" };
  if (to.startsWith("checklist:")) return { path, kind: "checklist", blockId: "", key: to.slice("checklist:".length) };
  if (to.startsWith("var:")) return { path, kind: "var", blockId: "", key: to.slice("var:".length) };
  return { path, kind: "status", blockId: "", key: "", rawTo: binding.to };
}

function bindingFromRow(row: BindingRow): ToolBinding {
  const to = row.rawTo ?? bindingTargetToString({ kind: row.kind, blockId: row.blockId, key: row.key });
  return { path: row.path, to };
}

/** A binding row's key/id sub-field is unset — the row isn't a complete target yet. */
function rowIncomplete(row: BindingRow): boolean {
  if (row.rawTo !== undefined) return false;
  if (row.kind === "details") return row.blockId === "" || row.key === "";
  if (row.kind === "table") return row.blockId === "";
  if (row.kind === "checklist" || row.kind === "var") return row.key === "";
  return false;
}

/** Whether `bindings` has a row Save should refuse (an incomplete target, or a bad path/name) — for the caller's Save gate. */
export function bindingsHaveIssues(bindings: ToolBinding[]): boolean {
  if (bindings.length > MAX_BINDINGS) return true;
  return bindings.some((binding) => {
    const row = rowFromBinding(binding);
    if (rowIncomplete(row)) return true;
    if (bindingPathIssue(row.path)) return true;
    if (row.kind === "var" && row.key !== "" && !VARIABLE_NAME_PATTERN.test(row.key)) return true;
    return false;
  });
}

/**
 * "Put the result on the panel" (`bindings`, D-V6-23): copy part of a successful result onto
 * the panel or into a variable, with no model turn. `path` is a JSON pointer into the result
 * (after the tool's own "Result JSON pointer" narrowing) — worded as "which part of the
 * result" here (§0.1: no "JSON pointer" in a string the caller — or the builder — sees).
 */
export function BindingsEditor({
  uid,
  values,
  onChange,
  detailsBlocks,
  tableBlocks,
}: {
  uid: string;
  values: ToolBinding[];
  onChange: (next: ToolBinding[]) => void;
  detailsBlocks: { id: string; title: string; fieldKeys: string[] }[];
  tableBlocks: { id: string; title: string }[];
}) {
  const rows = values.map(rowFromBinding);
  const atCap = rows.length >= MAX_BINDINGS;
  const hasBlocks = detailsBlocks.length > 0 || tableBlocks.length > 0;

  function setRows(next: BindingRow[]) {
    onChange(next.map(bindingFromRow));
  }

  function addRow() {
    if (atCap) return;
    setRows([...rows, { path: "", kind: hasBlocks && detailsBlocks.length > 0 ? "details" : "status", blockId: detailsBlocks[0]?.id ?? "", key: "" }]);
  }

  function patchRow(index: number, patch: Partial<BindingRow>) {
    setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function removeRow(index: number) {
    setRows(rows.filter((_, i) => i !== index));
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium text-foreground">Put the result on the panel</h3>
        <p className="text-[0.8125rem] text-pretty text-muted-foreground">
          After a successful call, copy part of the result onto the panel or into a variable — before the model
          replies, with no extra turn.
        </p>
      </div>
      {rows.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Which part of the result</TableHead>
              <TableHead>Goes to</TableHead>
              <TableHead>Where</TableHead>
              <TableHead className="sr-only">Remove</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, index) => (
              <TableRow key={index}>
                <TableCell>
                  <Input
                    value={row.path}
                    onChange={(e) => patchRow(index, { path: e.target.value })}
                    placeholder="/policy/holder"
                    aria-label={`Binding ${index + 1} — which part of the result`}
                    aria-invalid={bindingPathIssue(row.path) !== null}
                    className="w-40 font-mono text-xs"
                  />
                  {bindingPathIssue(row.path) ? (
                    <p className="mt-1 text-[0.6875rem] text-danger-text">Leave it blank for the whole result, or start with &apos;/&apos;.</p>
                  ) : null}
                </TableCell>
                <TableCell>
                  <Select
                    value={row.kind}
                    onValueChange={(value) => patchRow(index, { kind: value as BindingKind, rawTo: undefined, blockId: "", key: "" })}
                  >
                    <SelectTrigger className="w-40" aria-label={`Binding ${index + 1} — goes to`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {KINDS.map((kind) => (
                        <SelectItem key={kind} value={kind}>
                          {KIND_LABEL[kind]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell>
                  <BindingWhere row={row} detailsBlocks={detailsBlocks} tableBlocks={tableBlocks} onChange={(patch) => patchRow(index, patch)} rowIndex={index} />
                </TableCell>
                <TableCell>
                  <Button type="button" variant="ghost" size="icon" onClick={() => removeRow(index)} aria-label={`Remove binding ${index + 1}`}>
                    <Trash2Icon className="size-4" aria-hidden="true" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-[0.8125rem] text-muted-foreground">No bindings yet.</p>
      )}
      <Button type="button" variant="outline" size="sm" className="w-fit gap-1.5" onClick={addRow} disabled={atCap}>
        <PlusIcon className="size-3.5" aria-hidden="true" />
        Add a binding
      </Button>
      {atCap ? <p className="text-[0.8125rem] text-warning-text">At most {MAX_BINDINGS} bindings per tool.</p> : null}
      {rows.some(rowIncomplete) ? (
        <p id={`${uid}-bindings-error`} className="text-[0.8125rem] text-danger-text">
          Finish choosing where each binding goes before saving.
        </p>
      ) : null}
    </div>
  );
}

function BindingWhere({
  row,
  detailsBlocks,
  tableBlocks,
  onChange,
  rowIndex,
}: {
  row: BindingRow;
  detailsBlocks: { id: string; title: string; fieldKeys: string[] }[];
  tableBlocks: { id: string; title: string }[];
  onChange: (patch: Partial<BindingRow>) => void;
  rowIndex: number;
}) {
  if (row.kind === "details") {
    const block = detailsBlocks.find((b) => b.id === row.blockId);
    return (
      <div className="flex flex-col gap-1.5 sm:flex-row">
        {detailsBlocks.length > 0 ? (
          <Select value={row.blockId} onValueChange={(value) => onChange({ blockId: value })}>
            <SelectTrigger className="w-36" aria-label={`Binding ${rowIndex + 1} — card`}>
              <SelectValue placeholder="Card…" />
            </SelectTrigger>
            <SelectContent>
              {detailsBlocks.map((b) => (
                <SelectItem key={b.id} value={b.id}>
                  {b.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Input
            value={row.blockId}
            onChange={(e) => onChange({ blockId: e.target.value })}
            placeholder="card"
            aria-label={`Binding ${rowIndex + 1} — card`}
            className="w-28 font-mono text-xs"
          />
        )}
        <Input
          value={row.key}
          onChange={(e) => onChange({ key: e.target.value })}
          placeholder="field key"
          list={block && block.fieldKeys.length > 0 ? `${rowIndex}-details-keys` : undefined}
          aria-label={`Binding ${rowIndex + 1} — field key`}
          className="w-28 font-mono text-xs"
        />
        {block && block.fieldKeys.length > 0 ? (
          <datalist id={`${rowIndex}-details-keys`}>
            {block.fieldKeys.map((key) => (
              <option key={key} value={key} />
            ))}
          </datalist>
        ) : null}
      </div>
    );
  }
  if (row.kind === "table") {
    return tableBlocks.length > 0 ? (
      <Select value={row.blockId} onValueChange={(value) => onChange({ blockId: value })}>
        <SelectTrigger className="w-40" aria-label={`Binding ${rowIndex + 1} — table`}>
          <SelectValue placeholder="Table…" />
        </SelectTrigger>
        <SelectContent>
          {tableBlocks.map((b) => (
            <SelectItem key={b.id} value={b.id}>
              {b.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    ) : (
      <Input
        value={row.blockId}
        onChange={(e) => onChange({ blockId: e.target.value })}
        placeholder="table"
        aria-label={`Binding ${rowIndex + 1} — table`}
        className="w-32 font-mono text-xs"
      />
    );
  }
  if (row.kind === "checklist") {
    return (
      <Input
        value={row.key}
        onChange={(e) => onChange({ key: e.target.value })}
        placeholder="item id"
        aria-label={`Binding ${rowIndex + 1} — checklist item id`}
        className="w-32 font-mono text-xs"
      />
    );
  }
  if (row.kind === "var") {
    const invalid = row.key !== "" && !VARIABLE_NAME_PATTERN.test(row.key);
    return (
      <div className="flex flex-col gap-1">
        <Input
          value={row.key}
          onChange={(e) => onChange({ key: e.target.value })}
          placeholder="variable_name"
          aria-label={`Binding ${rowIndex + 1} — variable name`}
          className="w-32 font-mono text-xs"
        />
        {invalid ? <span className="text-[0.6875rem] text-danger-text">Lower case, digits, _.</span> : null}
      </div>
    );
  }
  return <span className="text-xs text-muted-foreground">—</span>;
}
