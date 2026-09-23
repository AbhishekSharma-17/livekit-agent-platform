"use client";

/**
 * `table` block — rows the agent appends (`table_append`): `columns
 * [{key, label, type}]`, `rows [{id, ...cells}]`, `selected_row`. When a row
 * brings a new key the worker sets `columns` first, in the same patch.
 *
 * Loaded lazily by `<Block>` (session bundle budget).
 */
import * as React from "react";

import type { TableBlockState, TableColumn } from "@/contracts/lkap-contracts";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

/** One cell as text, by column type. */
export function formatCell(value: unknown, type: TableColumn["type"] = "string"): string {
  if (value === undefined || value === null || value === "") return "—";
  switch (type) {
    case "boolean":
      return value === true || value === "true" ? "Yes" : value === false || value === "false" ? "No" : String(value);
    case "number": {
      const n = typeof value === "number" ? value : Number(value);
      return Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 6 }) : String(value);
    }
    case "date": {
      if (typeof value !== "string" && typeof value !== "number") return String(value);
      // A bare `YYYY-MM-DD` is a calendar date: show it as written, no timezone shift.
      if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
      const text = formatDateTime(value);
      return text === "—" || text === "" ? String(value) : text;
    }
    default:
      return typeof value === "object" ? JSON.stringify(value) : String(value);
  }
}

function columnsOf(data: TableBlockState): TableColumn[] {
  const columns = Array.isArray(data.columns) ? data.columns : [];
  if (columns.length > 0) return columns;
  // No declared columns yet: derive them from the rows' keys (minus `id`).
  const keys: string[] = [];
  for (const row of Array.isArray(data.rows) ? data.rows : []) {
    for (const key of Object.keys(row)) if (key !== "id" && !keys.includes(key)) keys.push(key);
  }
  return keys.map((key) => ({ key, label: key, type: "string" }));
}

export function TableBlock({ spec, data, title, highlighted }: BlockRenderProps<TableBlockState>) {
  const columns = columnsOf(data);
  const rows = Array.isArray(data.rows) ? data.rows : [];

  return (
    <BlockFrame spec={spec} title={title} count={rows.length} highlighted={highlighted}>
      {rows.length === 0 ? (
        <PanelEmpty>No rows yet.</PanelEmpty>
      ) : (
        <div
          data-slot="block-table"
          role="region"
          aria-label={title ?? "Table"}
          tabIndex={0}
          className="border-border focus-visible:ring-ring -mx-1 overflow-x-auto rounded-md border focus-visible:ring-2 focus-visible:outline-none"
        >
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="bg-muted/50">
                {columns.map((column) => (
                  <th
                    key={column.key}
                    scope="col"
                    className={cn(
                      "text-muted-foreground px-2.5 py-1.5 text-left text-xs font-medium whitespace-nowrap",
                      column.type === "number" && "text-right",
                    )}
                  >
                    {column.label || column.key}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const id = typeof row.id === "string" ? row.id : String(index);
                const selected = data.selected_row !== null && data.selected_row !== undefined && data.selected_row === id;
                return (
                  <tr
                    key={id}
                    data-row-id={id}
                    data-selected={selected ? "true" : undefined}
                    className={cn("border-border border-t", selected && "bg-brand-soft")}
                  >
                    {columns.map((column, columnIndex) => (
                      <td
                        key={column.key}
                        className={cn(
                          "px-2.5 py-1.5 align-top",
                          column.type === "number" && "text-right tabular-nums",
                          column.type === "date" && "whitespace-nowrap tabular-nums",
                        )}
                      >
                        {formatCell(row[column.key], column.type)}
                        {selected && columnIndex === 0 && <span className="sr-only"> (selected)</span>}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </BlockFrame>
  );
}

export default TableBlock;
