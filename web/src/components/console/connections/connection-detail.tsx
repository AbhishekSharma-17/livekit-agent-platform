"use client";

import * as React from "react";
import { toast } from "sonner";
import { RefreshCwIcon } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/shared/page-header";
import { LoadingRegion } from "@/components/shared/loading-state";
import { StatusPill } from "@/components/shared/status-chip";
import { ConnectionDetailTabs } from "@/components/console/connections/connection-detail-tabs";
import {
  connectionStatusLabel,
  connectionStatusTone,
  DEPLOYMENT_TYPE_LABEL,
} from "@/components/console/connections/connection-model";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { IfCan, ReadOnlyNote, readOnlyCopy } from "@/components/console/shared/permission";
import { ConsoleBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import { useConnection, useTestConnection } from "@/hooks/useConnections";
import { cn } from "@/lib/utils";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

const BACK = { href: "/console/connections", label: "Back to connections" };

const MODE_PHRASE: Record<NonNullable<ConnectionOut["deployment_mode"]>, string> = {
  external: "workers you run yourself",
  supervised: "workers LKAP supervises",
  cloud_hosted: "workers hosted on LiveKit Cloud",
};

/** "A LiveKit Cloud connection with workers LKAP supervises." */
function describeConnection(connection: ConnectionOut): string {
  const type = DEPLOYMENT_TYPE_LABEL[connection.deployment_type ?? "cloud"];
  return `A ${type} connection with ${MODE_PHRASE[connection.deployment_mode ?? "external"]}.`;
}

/**
 * Loading state for the connection page: mirrors the header (back link,
 * title, one-line intro, actions), the tab strip and the two-column
 * Overview (work cards on the left, facts on the right). Also the page's
 * `Suspense` fallback, so it is never a blank area.
 */
export function ConnectionDetailSkeleton() {
  return (
    <LoadingRegion label="Loading connection" className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-7 w-56" />
          <Skeleton className="h-4 w-80 max-w-full" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-[34px] w-24" />
          <Skeleton className="h-[34px] w-36" />
        </div>
      </div>
      <Skeleton className="h-10 w-full max-w-md" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,360px)]">
        <div className="flex flex-col gap-6">
          <Skeleton className="h-40 w-full rounded-lg" />
          <Skeleton className="h-56 w-full rounded-lg" />
        </div>
        <div className="flex flex-col gap-6">
          <Skeleton className="h-64 w-full rounded-lg" />
          <Skeleton className="h-28 w-full rounded-lg" />
        </div>
      </div>
    </LoadingRegion>
  );
}

/**
 * `/console/connections/[id]?tab=` — the Detail / record archetype
 * (docs/ui/DESIGN-SYSTEM.md section 7.4): a back link, the name with its
 * status, one sentence on what it is, then Refresh and the one primary
 * ("Test connection", admins only; everyone else reads a note, D12). The
 * test's findings and the last recorded failure are page-level alerts above
 * the tabs. Needs `Suspense` in the page for `useSearchParams`.
 */
export function ConnectionDetail({ connectionId }: { connectionId: string }) {
  const { data: connection, isLoading, isError, error, refetch, isFetching } = useConnection(connectionId);
  const testConnection = useTestConnection();
  // V6-27: the agent-name findings of the last test stay on the page until the next one.
  const [testWarnings, setTestWarnings] = React.useState<string[]>([]);

  if (isLoading) return <ConnectionDetailSkeleton />;

  if (isError || !connection) {
    return (
      <div>
        <PageHeader back={BACK} title="Connection" />
        <ErrorBanner error={error} context={{ action: "load this connection" }} onRetry={() => void refetch()} />
      </div>
    );
  }

  async function runTest(target: ConnectionOut) {
    try {
      const result = await testConnection.mutateAsync(target.id);
      setTestWarnings(result.warnings ?? []);
      if (result.ok) toast.success(`${target.name} is working.`);
      else toast.error(`${target.name} didn't pass the test. ${result.message}`);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <div>
      <ConsoleBreadcrumbs trail={[{ label: "Connections", href: "/console/connections" }, { label: connection.name }]} />
      <PageHeader
        back={BACK}
        title={connection.name}
        badge={
          <StatusPill tone={connectionStatusTone(connection.status)}>{connectionStatusLabel(connection.status)}</StatusPill>
        }
        description={describeConnection(connection)}
        actions={
          <>
            <Button type="button" onClick={() => void refetch()} disabled={isFetching}>
              <RefreshCwIcon aria-hidden="true" className={cn(isFetching && "animate-spin")} />
              Refresh
            </Button>
            <IfCan min="admin" fallback={<ReadOnlyNote>{readOnlyCopy("admin", "test or change this connection")}</ReadOnlyNote>}>
              <Button
                type="button"
                variant="primary"
                onClick={() => void runTest(connection)}
                busy={testConnection.isPending}
                busyLabel="Testing…"
              >
                Test connection
              </Button>
            </IfCan>
          </>
        }
      />

      <div className="mb-6 flex flex-col gap-3 empty:hidden">
        {connection.last_error ? (
          <Alert tone="danger" title="The last test failed">
            <span className="block">{connection.last_error}</span>
            Check the URL, key and secret, then test again.
          </Alert>
        ) : null}
        {testWarnings.length > 0 ? (
          <Alert tone="warning" title="The test found something to check">
            <ul className="flex list-none flex-col gap-1 p-0">
              {testWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Alert>
        ) : null}
      </div>

      <ConnectionDetailTabs connection={connection} />
    </div>
  );
}
