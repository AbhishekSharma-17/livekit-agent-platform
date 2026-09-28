"use client";

import * as React from "react";
import { SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/shared/icon";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/console/shared/empty-state";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useDatasetLookup } from "@/components/console/lib/api-hooks";
import type { DatasetKeyColumn, DatasetOut } from "@/contracts/lkap-contracts";

/**
 * "Test a lookup" (ask #104): one value per key column, exact or prefix match, run through
 * the same route (`POST /v1/datasets/{id}/lookup`) an agent's `dataset` tool calls — a
 * builder write (`agents:write`), like the knowledge base's test search. 409 while the table
 * is still importing and 422 for a bad value both show as a plain inline message.
 */
export function DatasetLookupTest({ dataset }: { dataset: DatasetOut }) {
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [match, setMatch] = React.useState<"exact" | "prefix">("exact");
  const lookup = useDatasetLookup(dataset.id);
  const [message, setMessage] = React.useState<string | null>(null);

  const keyColumns: DatasetKeyColumn[] = dataset.key_columns;
  const filled = keyColumns.filter((column) => values[column.name]?.trim());

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setMessage(null);
    const keys = Object.fromEntries(
      keyColumns.map((column) => [column.name, values[column.name] ?? ""]).filter(([, value]) => value.trim() !== ""),
    );
    if (Object.keys(keys).length === 0) {
      setMessage("Enter at least one value to look up.");
      return;
    }
    try {
      await lookup.mutateAsync({ keys, match, max_rows: 5 });
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  const result = lookup.data;

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-foreground">Test a lookup</h2>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-3">
          {keyColumns.map((column) => (
            <div key={column.name} className="flex flex-col gap-1">
              <label htmlFor={`lookup-${column.name}`} className="text-xs font-medium text-muted-foreground">
                {column.name}
              </label>
              <Input
                id={`lookup-${column.name}`}
                className="w-40 font-mono text-sm"
                value={values[column.name] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [column.name]: e.target.value }))}
              />
            </div>
          ))}
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">Match</span>
            <Select value={match} onValueChange={(v) => setMatch(v as "exact" | "prefix")}>
              <SelectTrigger className="w-36" aria-label="Match">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="exact">Exactly</SelectItem>
                <SelectItem value="prefix">Starts with</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" className="self-end" disabled={lookup.isPending || filled.length === 0}>
            <Icon as={SearchIcon} size="sm" /> {lookup.isPending ? "Looking up…" : "Look up"}
          </Button>
        </div>
      </form>

      {message ? <p className="text-[0.8125rem] text-danger-text">{message}</p> : null}

      {result ? (
        result.rows.length === 0 ? (
          <EmptyState compact icon={SearchIcon} title="No matches" description="Try a different value or match type." />
        ) : (
          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  {Object.keys(result.rows[0]).map((key) => (
                    <TableHead key={key} className="whitespace-nowrap">
                      {key}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.rows.map((row, index) => (
                  <TableRow key={index}>
                    {Object.keys(result.rows[0]).map((key) => (
                      <TableCell key={key} className="max-w-64 truncate font-mono text-xs">
                        {row[key] ?? ""}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {result.truncated ? (
              <p className="p-2 text-[0.8125rem] text-muted-foreground">More rows matched than shown here.</p>
            ) : null}
          </div>
        )
      ) : null}
    </div>
  );
}
