"use client";

import * as React from "react";
import { toast } from "sonner";
import { AlertCircleIcon, Loader2Icon, Table2Icon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/shared/icon";
import { PageHeader } from "@/components/console/shared/page-header";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { StatusChip } from "@/components/shared/status-chip";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { RelativeTime } from "@/components/shared/relative-time";
import { SkeletonRows } from "@/components/shared/loading-state";
import { useDatasets, useDeleteDataset } from "@/components/console/lib/api-hooks";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { UploadDatasetDialog } from "@/components/console/datasets/upload-dataset-dialog";
import { pluralize } from "@/lib/format";
import type { DatasetOut } from "@/contracts/lkap-contracts";

/** "Importing…" / "Ready" / "Failed" — mirrors `kb-documents.tsx`'s `DocumentStatusChip`. */
function DatasetStatusChip({ dataset }: { dataset: DatasetOut }) {
  if (dataset.status === "pending") {
    const pct = typeof dataset.progress === "number" ? Math.round(Math.max(0, Math.min(1, dataset.progress)) * 100) : null;
    return (
      <StatusChip tone="info">
        <Icon as={Loader2Icon} size="sm" className="animate-spin" /> Importing{pct !== null ? ` ${pct}%` : "…"}
      </StatusChip>
    );
  }
  if (dataset.status === "failed") {
    return <StatusChip tone="danger">Failed</StatusChip>;
  }
  return <StatusChip tone="success">Ready</StatusChip>;
}

/**
 * `/console/datasets` (V6-19; the console calls these "lookup tables" everywhere the caller
 * sees them, matching `tool-row.tsx`'s existing wording): every lookup table of the workspace,
 * its import status, delete with a confirm — 409 (a tool still uses it) shown verbatim.
 */
export function DatasetList() {
  const { data, isLoading, isError, error, refetch } = useDatasets({ pollWhilePending: true });
  const deleteDataset = useDeleteDataset();
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  const header = <PageHeader title="Lookup tables" description="Read-only tables an agent looks a caller up in." actions={<UploadDatasetDialog />} />;

  if (isLoading) {
    return (
      <div>
        {header}
        <SkeletonRows label="Loading lookup tables" rowClassName="h-12" />
      </div>
    );
  }

  if (isError) {
    return (
      <div>
        {header}
        <ErrorBanner message={`Couldn't load lookup tables — ${errorMessage(error)}`} onRetry={() => refetch()} />
      </div>
    );
  }

  const datasets = data?.items ?? [];

  async function handleDelete(dataset: DatasetOut) {
    try {
      await deleteDataset.mutateAsync(dataset.id);
      toast.success(`${dataset.name} deleted.`);
    } catch (error_) {
      toast.error(errorMessage(error_));
    }
  }

  const columns: ResponsiveTableColumn<DatasetOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (dataset) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{dataset.name}</div>
          {dataset.status === "failed" && dataset.error ? (
            <div className="truncate text-xs text-danger-text">{dataset.error}</div>
          ) : null}
        </div>
      ),
    },
    {
      id: "keys",
      header: "Matches on",
      cell: (dataset) => (
        <span className="text-muted-foreground">{dataset.key_columns.map((column) => column.name).join(", ") || "—"}</span>
      ),
    },
    {
      id: "rows",
      header: "Rows",
      cell: (dataset) => <span className="tabular-nums">{dataset.row_count}</span>,
    },
    {
      id: "status",
      header: "Status",
      cell: (dataset) => <DatasetStatusChip dataset={dataset} />,
    },
    {
      id: "updated",
      header: "Updated",
      cell: (dataset) => <RelativeTime iso={dataset.updated_at} />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (dataset) => (
        <ConfirmDialog
          trigger={
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Delete ${dataset.name}`}
              disabled={!canWrite}
              title={canWrite ? undefined : writeReason}
            >
              <Icon as={Trash2Icon} size="sm" />
            </Button>
          }
          title={`Delete "${dataset.name}"?`}
          description="Deletes the table and its rows. Refused while a tool still uses it."
          onConfirm={() => handleDelete(dataset)}
        />
      ),
    },
  ];

  return (
    <div>
      {header}
      <ResponsiveTable
        columns={columns}
        rows={datasets}
        label="Lookup tables"
        rowHref={(dataset) => `/console/datasets/${dataset.id}`}
        getRowKey={(dataset) => dataset.id}
        renderCard={(dataset) => (
          <div className="flex flex-col gap-1">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">{dataset.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  Matches on {dataset.key_columns.map((column) => column.name).join(", ") || "—"}
                </div>
              </div>
              <DatasetStatusChip dataset={dataset} />
            </div>
            <div className="text-xs text-muted-foreground">{pluralize(dataset.row_count, "row", "rows")}</div>
          </div>
        )}
        empty={
          <EmptyState
            icon={Table2Icon}
            title="No lookup tables yet"
            description="Upload a spreadsheet an agent can look a caller up in."
            action={<UploadDatasetDialog />}
          />
        }
      />
      {datasets.some((d) => d.status === "failed") ? (
        <p className="mt-2 flex items-center gap-1.5 text-[0.8125rem] text-muted-foreground">
          <Icon as={AlertCircleIcon} size="sm" /> A failed import can be deleted and uploaded again.
        </p>
      ) : null}
    </div>
  );
}
