"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { StatusChip } from "@/components/shared/status-chip";
import { ConnectionDetailTabs } from "@/components/console/connections/connection-detail-tabs";
import { connectionStatusTone, connectionStatusLabel } from "@/components/console/connections/connection-model";
import { errorMessage, ErrorBanner } from "@/components/console/shared/error-banner";
import { useConnection } from "@/hooks/useConnections";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { LoadingRegion } from "@/components/shared/loading-state";

/** `/console/connections/[id]?tab=` (needs `Suspense` in the page for `useSearchParams`, WP-1's convention). */
export function ConnectionDetail({ connectionId }: { connectionId: string }) {
  const { data: connection, isLoading, isError, error, refetch } = useConnection(connectionId);

  if (isLoading) {
    return (
      <LoadingRegion label="Loading connection" className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </LoadingRegion>
    );
  }

  if (isError || !connection) {
    return <ErrorBanner message={`Couldn't load this connection — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  return (
    <div>
      <ConsoleBreadcrumbs trail={[{ label: "Connections", href: "/console/connections" }, { label: connection.name }]} />
      <PageHeader
        title={connection.name}
        description={connection.url}
        actions={
          <StatusChip tone={connectionStatusTone(connection.status)} dot>
            {connectionStatusLabel(connection.status)}
          </StatusChip>
        }
      />
      <ConnectionDetailTabs connection={connection} />
    </div>
  );
}
