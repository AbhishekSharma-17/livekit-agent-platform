"use client";

import * as React from "react";
import { TableIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { EmptyState } from "@/components/console/shared/empty-state";
import { useDatasetRows } from "@/components/console/lib/api-hooks";

const PAGE_SIZE = 25;

/**
 * A page of a lookup table's rows, in file order (ask #104: "GET …/rows previews"). Cells are
 * raw text — rendered as plain React text nodes, never HTML or a formula, matching the api's
 * own rule that nothing in a cell is ever evaluated.
 */
export function DatasetRowsPreview({ datasetId, ready }: { datasetId: string; ready: boolean }) {
  const [offset, setOffset] = React.useState(0);
  const { data, isLoading, isError, error, refetch } = useDatasetRows(datasetId, offset, PAGE_SIZE, { enabled: ready });

  if (!ready) {
    return <p className="text-body text-text-secondary">Rows show up here once the import finishes.</p>;
  }

  if (isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }

  if (isError) {
    return <ErrorBanner message={`Couldn't load rows — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const rows = data?.rows ?? [];
  const columns = data?.columns ?? [];
  const total = data?.total ?? 0;

  if (rows.length === 0) {
    return <EmptyState compact icon={TableIcon} title="No rows" description="This table has no rows yet." />;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto rounded border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column.name} className="whitespace-nowrap">
                  {column.label || column.name}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, index) => (
              <TableRow key={offset + index}>
                {columns.map((column) => (
                  <TableCell key={column.name} className="max-w-64 truncate font-mono text-caption">
                    {row[column.name] ?? ""}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-between text-label text-text-secondary">
        <span>
          {offset + 1}–{Math.min(offset + rows.length, total)} of {total}
        </span>
        <div className="flex gap-2">
          <Button type="button" variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Previous
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={offset + rows.length >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
