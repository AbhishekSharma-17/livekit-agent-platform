"use client";

import * as React from "react";
import { SearchIcon, SearchXIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Field, FormError } from "@/components/shared/field";
import { EmptyState } from "@/components/console/shared/empty-state";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useDatasetLookup } from "@/components/console/lib/api-hooks";
import type { DatasetKeyColumn, DatasetOut } from "@/contracts/lkap-contracts";

/**
 * "Test a lookup" (ask #104): one value per key column, exact or prefix match, run through
 * the same route (`POST /v1/datasets/{id}/lookup`) an agent's `dataset` tool calls — a
 * builder write (`agents:write`), like the knowledge base's test search. 409 while the table
 * is still importing and 422 for a bad value both show as a plain form-level message; what
 * was typed stays put. Look up is the detail page's one primary action.
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
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Test a lookup</h2>
        </CardTitle>
        <CardDescription>Runs the same lookup an agent&apos;s tool makes, and shows up to five matching rows.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-3">
            {keyColumns.map((column) => (
              <Field key={column.name} label={<span className="font-mono">{column.name}</span>} htmlFor={`lookup-${column.name}`} className="w-full sm:w-44">
                <Input
                  id={`lookup-${column.name}`}
                  className="font-mono"
                  value={values[column.name] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [column.name]: e.target.value }))}
                />
              </Field>
            ))}
            <Field label="Match" htmlFor="lookup-match" className="w-full sm:w-40">
              <Select value={match} onValueChange={(v) => setMatch(v as "exact" | "prefix")}>
                <SelectTrigger id="lookup-match" className="w-full" aria-label="Match">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="exact">Exactly</SelectItem>
                  <SelectItem value="prefix">Starts with</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Button type="submit" variant="primary" disabled={filled.length === 0} busy={lookup.isPending} busyLabel="Looking up…">
              <SearchIcon aria-hidden="true" /> Look up
            </Button>
          </div>
          <FormError>{message}</FormError>
        </form>

        {result ? (
          result.rows.length === 0 ? (
            <EmptyState compact icon={SearchXIcon} title="No matching rows" description="Try a different value or match type." />
          ) : (
            <div className="flex flex-col gap-2">
              <Table framed>
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
                        <TableCell key={key} className="max-w-64 truncate font-mono text-caption">
                          {row[key] ?? ""}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {result.truncated ? <p className="text-label text-text-secondary">More rows matched than shown here.</p> : null}
            </div>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}
