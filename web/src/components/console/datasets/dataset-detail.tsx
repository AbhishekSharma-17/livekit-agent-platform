"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Alert } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DescriptionList } from "@/components/shared/description-list";
import { LoadingRegion } from "@/components/shared/loading-state";
import { Page, PageHeader } from "@/components/shared/page-header";
import { RelativeTime } from "@/components/shared/relative-time";
import { Tag, TagList } from "@/components/shared/tag";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { useDataset, useDeleteDataset } from "@/components/console/lib/api-hooks";
import { DatasetRowsPreview } from "@/components/console/datasets/dataset-rows-preview";
import { DatasetLookupTest } from "@/components/console/datasets/dataset-lookup-test";
import { DatasetStatus, IMPORT_FAILED_FALLBACK } from "@/components/console/datasets/dataset-list";
import { DangerZoneCard } from "@/components/console/shared/danger-zone-card";
import { plainStatusError } from "@/components/shared/status-error";
import { useWriteGate } from "@/components/console/shared/write-gate";
import { EMPTY_VALUE, pluralize } from "@/lib/format";
import type { DatasetOut } from "@/contracts/lkap-contracts";

const BACK = { href: "/console/datasets", label: "Back to lookup tables" };

/**
 * `/console/datasets/{id}` (V6-19) as the detail archetype (docs/ui/DESIGN-SYSTEM.md
 * section 7.4): a back link, the name with its status pill, one sentence, and a
 * page-level alert when the import failed. The main column holds the rows and the
 * test lookup (its Look up is the page's one primary); the side column holds the facts
 * and, for builders, the Danger zone — delete is no longer the only header action.
 */
export function DatasetDetail({ datasetId }: { datasetId: string }) {
  const { data: dataset, isLoading, isError, error, refetch } = useDataset(datasetId, { poll: true });
  const gate = useWriteGate();

  if (isLoading) {
    return <DatasetDetailSkeleton />;
  }

  if (isError || !dataset) {
    return (
      <Page width="wide">
        <PageHeader back={BACK} title="Lookup table" />
        <ErrorBanner error={error} context={{ action: "load this lookup table" }} onRetry={() => refetch()} />
      </Page>
    );
  }

  const ready = dataset.status === "ready";
  const keyNames = dataset.key_columns.map((column) => column.name).join(", ");

  return (
    <Page width="wide">
      <ConsoleBreadcrumbs trail={[{ label: "Lookup tables", href: "/console/datasets" }, { label: dataset.name }]} />
      <PageHeader
        back={BACK}
        title={dataset.name}
        badge={<DatasetStatus dataset={dataset} />}
        description={
          dataset.status === "pending"
            ? "Importing the file. This page updates by itself when it's done."
            : dataset.status === "failed"
              ? "The import didn't finish, so agents can't look anything up here yet."
              : `${pluralize(dataset.row_count, "row", "rows")}, matched on ${keyNames || "no columns"}.`
        }
      />

      {dataset.status === "failed" ? (
        <Alert tone="danger" title="The import failed" className="mb-6">
          {plainStatusError(dataset.error, IMPORT_FAILED_FALLBACK)} Delete this table and upload the file again.
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
        <div className="flex min-w-0 flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Rows</h2>
              </CardTitle>
              <CardDescription>A page of the table, in file order. Cells are plain text; nothing in them runs.</CardDescription>
            </CardHeader>
            <CardContent>
              <DatasetRowsPreview datasetId={dataset.id} ready={ready} />
            </CardContent>
          </Card>
          {ready ? <DatasetLookupTest dataset={dataset} /> : null}
        </div>
        <aside className="flex min-w-0 flex-col gap-6" aria-label="About this lookup table">
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Details</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <DescriptionList
                items={[
                  { term: "Rows", detail: dataset.row_count, mono: true },
                  { term: "Columns", detail: dataset.columns.length, mono: true },
                  {
                    term: "Match on",
                    detail:
                      dataset.key_columns.length > 0 ? (
                        <TagList>
                          {dataset.key_columns.map((column) => (
                            <Tag key={column.name}>
                              <span className="font-mono">{column.name}</span> · {column.type}
                            </Tag>
                          ))}
                        </TagList>
                      ) : (
                        EMPTY_VALUE
                      ),
                  },
                  { term: "Status", detail: <DatasetStatus dataset={dataset} size="sm" /> },
                  { term: "Updated", detail: <RelativeTime iso={dataset.updated_at} withExact /> },
                ]}
              />
            </CardContent>
          </Card>
          {gate.can ? <DatasetDangerZone dataset={dataset} /> : null}
        </aside>
      </div>
    </Page>
  );
}

/** Kept apart so the router is only read where a delete can actually happen. */
function DatasetDangerZone({ dataset }: { dataset: DatasetOut }) {
  const router = useRouter();
  const deleteDataset = useDeleteDataset();
  return (
    <DangerZoneCard
      actionLabel="Delete lookup table"
      description="Deleting is refused while a tool still uses this table. It can't be undone."
      confirmTitle={`Delete “${dataset.name}”?`}
      confirmDescription="This permanently deletes the lookup table. It's refused while a tool still uses it."
      onConfirm={async () => {
        await deleteDataset.mutateAsync(dataset.id);
        toast.success(`${dataset.name} deleted.`);
        router.push("/console/datasets");
      }}
    >
      <ul className="list-disc pl-5">
        <li className="tabular-nums">The table and its {pluralize(dataset.row_count, "row", "rows")}</li>
      </ul>
    </DangerZoneCard>
  );
}

/** Mirrors the page: header, the rows card and the side column's facts. */
function DatasetDetailSkeleton() {
  return (
    <Page width="wide">
      <LoadingRegion label="Loading lookup table">
        <div className="mb-6 flex flex-col gap-2">
          <Skeleton className="h-3.5 w-40" />
          <Skeleton className="h-7 w-64" />
          <Skeleton className="h-4 w-80 max-w-full" />
        </div>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Skeleton className="h-72 w-full rounded-lg" />
          <Skeleton className="h-56 w-full rounded-lg" />
        </div>
      </LoadingRegion>
    </Page>
  );
}
