"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2Icon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/shared/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/shared/status-chip";
import { DescriptionList } from "@/components/shared/description-list";
import { PageHeader } from "@/components/console/shared/page-header";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { useDataset, useDeleteDataset } from "@/components/console/lib/api-hooks";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { DatasetRowsPreview } from "@/components/console/datasets/dataset-rows-preview";
import { DatasetLookupTest } from "@/components/console/datasets/dataset-lookup-test";
import { LoadingRegion } from "@/components/shared/loading-state";
import { pluralize } from "@/lib/format";

/** `/console/datasets/{id}` (V6-19): status, columns, a row preview and the test lookup. */
export function DatasetDetail({ datasetId }: { datasetId: string }) {
  const router = useRouter();
  const { data: dataset, isLoading, isError, error, refetch } = useDataset(datasetId, { poll: true });
  const deleteDataset = useDeleteDataset();
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  if (isLoading) {
    return (
      <LoadingRegion label="Loading lookup table">
        <Skeleton className="mb-6 h-16 w-full" />
        <Skeleton className="h-64 w-full" />
      </LoadingRegion>
    );
  }

  if (isError || !dataset) {
    return <ErrorBanner message={`Couldn't load this lookup table — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  async function handleDelete() {
    try {
      await deleteDataset.mutateAsync(datasetId);
      toast.success(`${dataset!.name} deleted.`);
      router.push("/console/datasets");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  const ready = dataset.status === "ready";

  return (
    <div>
      <ConsoleBreadcrumbs trail={[{ label: "Lookup tables", href: "/console/datasets" }, { label: dataset.name }]} />
      <PageHeader
        title={dataset.name}
        description={
          dataset.status === "pending" ? (
            <span className="inline-flex items-center gap-1.5">
              <Icon as={Loader2Icon} size="sm" className="animate-spin" /> Importing…
            </span>
          ) : dataset.status === "failed" ? (
            <span className="text-danger-text">{dataset.error || "The import failed."}</span>
          ) : (
            `${pluralize(dataset.row_count, "row", "rows")} · matches on ${dataset.key_columns.map((c) => c.name).join(", ")}`
          )
        }
        actions={
          <ConfirmDialog
            trigger={
              <Button type="button" variant="outline" disabled={!canWrite} title={canWrite ? undefined : writeReason}>
                <Icon as={Trash2Icon} size="sm" /> Delete
              </Button>
            }
            title={`Delete "${dataset.name}"?`}
            description="Deletes the table and its rows. Refused while a tool still uses it."
            onConfirm={handleDelete}
          />
        }
      />

      <DescriptionList
        className="mb-6"
        columns={3}
        items={[
          { term: "Columns", detail: dataset.columns.length, mono: true },
          {
            term: "Match on",
            detail: (
              <div className="flex flex-wrap gap-1.5">
                {dataset.key_columns.map((column) => (
                  <StatusChip key={column.name} tone="neutral" size="sm">
                    {column.name} · {column.type}
                  </StatusChip>
                ))}
              </div>
            ),
          },
          { term: "Status", detail: dataset.status },
        ]}
      />

      <div className="flex flex-col gap-8">
        <div className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-foreground">Rows</h2>
          <DatasetRowsPreview datasetId={dataset.id} ready={ready} />
        </div>
        {ready ? <DatasetLookupTest dataset={dataset} /> : null}
      </div>
    </div>
  );
}
