"use client";

import * as React from "react";
import { toast } from "sonner";
import { Table2Icon, Trash2Icon } from "lucide-react";

import { IconButton } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/shared/page-header";
import { EmptyState, NoMatches } from "@/components/shared/empty-state";
import { LifecycleBadge } from "@/components/shared/status-chip";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { RelativeTime } from "@/components/shared/relative-time";
import { SkeletonRows } from "@/components/shared/loading-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { useDatasets, useDeleteDataset } from "@/components/console/lib/api-hooks";
import { UploadDatasetDialog } from "@/components/console/datasets/upload-dataset-dialog";
import { plainStatusError } from "@/components/shared/status-error";
import { Highlight, ListToolbar, SEARCH_THRESHOLD, matchesQuery, useRememberedChoice } from "@/components/shared/list-search";
import { useWriteGate } from "@/components/console/shared/write-gate";
import { EMPTY_VALUE, pluralize } from "@/lib/format";
import type { DatasetOut } from "@/contracts/lkap-contracts";

/** The worker's reason for a failed import, when it reads as plain copy. */
export const IMPORT_FAILED_FALLBACK = "The import failed. Check the file and upload it again.";

/** "Importing 40%" / "Ready" / "Failed", through the shared lifecycle map. */
export function DatasetStatus({ dataset, size }: { dataset: DatasetOut; size?: "sm" | "md" }) {
  if (dataset.status === "pending") {
    const pct = typeof dataset.progress === "number" ? Math.max(0, Math.min(1, dataset.progress)) * 100 : null;
    return <LifecycleBadge state="importing" progress={pct} size={size} />;
  }
  return <LifecycleBadge state={dataset.status === "failed" ? "failed" : "ready"} size={size} />;
}

type StatusFilter = "all" | "ready" | "pending" | "failed";
const STATUS_FILTERS: readonly StatusFilter[] = ["all", "ready", "pending", "failed"];
const STATUS_FILTER_LABEL: Record<Exclude<StatusFilter, "all">, string> = { ready: "Ready", pending: "Importing", failed: "Failed" };

function statusOf(dataset: DatasetOut): Exclude<StatusFilter, "all"> {
  return dataset.status === "pending" ? "pending" : dataset.status === "failed" ? "failed" : "ready";
}

const TITLE = "Lookup tables";
const DESCRIPTION = "Read-only tables an agent looks a caller up in.";

/**
 * `/console/datasets` (V6-19; the console calls these "lookup tables" everywhere the caller
 * sees them, matching `tool-row.tsx`'s existing wording): every lookup table of the workspace,
 * its import status, delete with a confirm — 409 (a tool still uses it) says so in the
 * api's own words. Search and a remembered status filter once there are six or more
 * (docs/ui/DESIGN-SYSTEM.md section 9); separate "nothing yet" and "no matches" states.
 */
export function DatasetList() {
  const { data, isLoading, isError, error, refetch } = useDatasets({ pollWhilePending: true });
  const deleteDataset = useDeleteDataset();
  const gate = useWriteGate();
  const [query, setQuery] = React.useState("");
  const [status, setStatus] = useRememberedChoice<StatusFilter>("datasets-status", "all", STATUS_FILTERS, { legacyKeys: ["lkap.datasets.status"] });

  const header = <PageHeader title={TITLE} description={DESCRIPTION} actions={<UploadDatasetDialog />} />;

  if (isLoading) {
    return (
      <Page>
        {header}
        <SkeletonRows label="Loading lookup tables" rows={3} rowClassName="h-14" />
      </Page>
    );
  }

  if (isError) {
    return (
      <Page>
        {header}
        <ErrorBanner error={error} context={{ action: "load lookup tables" }} onRetry={() => refetch()} />
      </Page>
    );
  }

  const datasets = data?.items ?? [];
  const keysOf = (dataset: DatasetOut) => dataset.key_columns.map((column) => column.name).join(", ");
  const filtering = query.trim() !== "" || status !== "all";
  const visible = datasets.filter(
    (dataset) => (status === "all" || statusOf(dataset) === status) && matchesQuery([dataset.name, keysOf(dataset)], query),
  );
  const counts: Record<Exclude<StatusFilter, "all">, number> = { ready: 0, pending: 0, failed: 0 };
  for (const dataset of datasets) counts[statusOf(dataset)] += 1;
  const statusOptions = [
    { value: "all" as StatusFilter, label: "All", count: datasets.length },
    ...(Object.keys(STATUS_FILTER_LABEL) as Array<Exclude<StatusFilter, "all">>)
      .filter((key) => counts[key] > 0 || key === status)
      .map((key) => ({ value: key as StatusFilter, label: STATUS_FILTER_LABEL[key], count: counts[key] })),
  ];
  const clearFilters = () => {
    setQuery("");
    setStatus("all");
  };

  async function handleDelete(dataset: DatasetOut) {
    try {
      await deleteDataset.mutateAsync(dataset.id);
      toast.success(`${dataset.name} deleted.`);
    } catch (error_) {
      toast.error(errorMessage(error_));
    }
  }

  const failedLine = (dataset: DatasetOut) =>
    dataset.status === "failed" ? (
      <div className="truncate text-caption text-destructive-text">{plainStatusError(dataset.error, IMPORT_FAILED_FALLBACK)}</div>
    ) : null;

  const columns: ResponsiveTableColumn<DatasetOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (dataset) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">
            <Highlight text={dataset.name} query={query} />
          </div>
          {failedLine(dataset)}
        </div>
      ),
    },
    {
      id: "keys",
      header: "Matches on",
      cell: (dataset) => (
        <span className="text-text-secondary">{keysOf(dataset) ? <Highlight text={keysOf(dataset)} query={query} /> : EMPTY_VALUE}</span>
      ),
    },
    {
      id: "rows",
      header: "Rows",
      align: "end",
      cell: (dataset) => <span className="tabular-nums">{dataset.row_count}</span>,
    },
    {
      id: "status",
      header: "Status",
      cell: (dataset) => <DatasetStatus dataset={dataset} />,
    },
    {
      id: "updated",
      header: "Updated",
      cell: (dataset) => <RelativeTime iso={dataset.updated_at} className="text-text-secondary" />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (dataset) =>
        gate.show ? (
          <ConfirmDialog
            trigger={
              <IconButton label={`Delete ${dataset.name}`} size="sm" disabled={gate.pending}>
                <Trash2Icon />
              </IconButton>
            }
            title={`Delete “${dataset.name}”?`}
            description="Deletes the table and its rows. It's refused while a tool still uses it."
            confirmLabel="Delete lookup table"
            onConfirm={() => handleDelete(dataset)}
          />
        ) : null,
    },
  ];

  return (
    <Page>
      {header}
      {datasets.length >= SEARCH_THRESHOLD || filtering ? (
        <ListToolbar
          items="lookup tables"
          query={query}
          onQueryChange={setQuery}
          filter={{ label: "Filter by status", value: status, onValueChange: setStatus, options: statusOptions }}
        />
      ) : null}
      <ResponsiveTable
        columns={columns}
        rows={visible}
        label="Lookup tables"
        rowHref={(dataset) => `/console/datasets/${dataset.id}`}
        getRowKey={(dataset) => dataset.id}
        renderCard={(dataset) => (
          <div className="flex flex-col gap-1">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">
                  <Highlight text={dataset.name} query={query} />
                </div>
                <div className="truncate text-caption text-text-secondary">Matches on {keysOf(dataset) || EMPTY_VALUE}</div>
                {failedLine(dataset)}
              </div>
              <DatasetStatus dataset={dataset} size="sm" />
            </div>
            <div className="text-caption text-text-secondary tabular-nums">{pluralize(dataset.row_count, "row", "rows")}</div>
          </div>
        )}
        empty={
          filtering ? (
            <NoMatches items="lookup tables" query={query} onClear={clearFilters} />
          ) : (
            <EmptyState
              icon={Table2Icon}
              title="No lookup tables yet"
              description={gate.show ? "Upload a spreadsheet an agent can look a caller up in." : "Nobody has uploaded a lookup table yet."}
              action={gate.show ? <UploadDatasetDialog variant="secondary" /> : undefined}
            />
          )
        }
      />
      {datasets.some((d) => d.status === "failed") ? (
        <p className="mt-3 text-label text-text-secondary">A failed import can be deleted and uploaded again.</p>
      ) : null}
    </Page>
  );
}
