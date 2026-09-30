"use client";

import * as React from "react";
import { toast } from "sonner";
import { MinusIcon, PlusIcon, RefreshCwIcon, ServerIcon } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { LoadingRegion } from "@/components/shared/loading-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import {
  fleetHealth,
  instanceStatusLabel,
  instanceStatusTone,
  visibleInstances,
} from "@/components/console/connections/connection-model";
import { WorkerStatusNotice } from "@/components/console/connections/worker-status-notice";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { IfCan, ReadOnlyNote, readOnlyCopy } from "@/components/console/shared/permission";
import { useConnectionFleet, useFleetAction } from "@/hooks/useConnections";
import type { ConnectionOut, WorkerInstanceOut } from "@/contracts/lkap-contracts";

const MANAGED_BY_LABEL: Record<NonNullable<WorkerInstanceOut["managed_by"]>, string> = {
  external: "external",
  supervisor: "supervisor",
  cloud: "cloud",
};

/** Loading state: mirrors the pool card (stat plus controls) and the instance rows. */
function FleetSkeleton() {
  return (
    <LoadingRegion label="Loading the worker pool" className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4 rounded-lg border border-border bg-card p-5">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-7 w-28" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-[34px] w-24" />
          <Skeleton className="h-[34px] w-20" />
          <Skeleton className="h-[34px] w-20" />
        </div>
      </div>
      <div className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
        {[0, 1].map((index) => (
          <div key={index} className="flex items-center gap-4 px-3.5 py-3">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="ml-auto h-5 w-16 rounded-pill" />
          </div>
        ))}
      </div>
    </LoadingRegion>
  );
}

/**
 * The Fleet tab (UI_UX_SPEC-V2-AMENDMENTS §2.1 "Detail → Fleet tab";
 * docs/v2/_asks.md #48, V2-04's hand-off): desired replicas stepper,
 * Start/Stop/Restart, and the instances table. Supervised connections only —
 * external/cloud-hosted pools have nothing here to manage (V2-04's fleet
 * routes 409 for them). Every mode shows V6-27's worker status first. Fleet
 * actions are admin-only server-side, so only admins see the controls (D12);
 * Stop asks first, since agents stop answering calls.
 */
export function FleetCard({ connection }: { connection: ConnectionOut }) {
  const supervised = connection.deployment_mode === "supervised";
  const { data, isLoading, isError, error, refetch } = useConnectionFleet(connection.id, { poll: true });
  const instances = React.useMemo(() => visibleInstances(data?.instances), [data?.instances]);
  const search = useListSearch("connection-instances", instances, (instance) => [
    instance.instance_key,
    instance.image,
    instance.sdk_version,
    instance.managed_by,
    instanceStatusLabel(instance.status),
  ]);

  if (!supervised) {
    return (
      <div className="flex flex-col gap-4">
        <WorkerStatusNotice connection={connection} />
        <Alert tone="info" title="This worker pool isn't managed here">
          This connection is {connection.deployment_mode === "cloud_hosted" ? "cloud-hosted" : "external"}.{" "}
          {connection.deployment_mode === "cloud_hosted"
            ? "The Deploy tab has the bundle to deploy its worker."
            : "The Deploy tab has the settings an external worker needs."}
        </Alert>
      </div>
    );
  }

  if (isLoading) return <FleetSkeleton />;
  if (isError) {
    return <ErrorBanner error={error} context={{ action: "load the worker pool" }} onRetry={() => void refetch()} />;
  }

  const health = fleetHealth(data?.desired_replicas, data?.instances);
  const query = search.query;

  const columns: ResponsiveTableColumn<WorkerInstanceOut>[] = [
    {
      id: "instance",
      header: "Instance",
      cell: (instance) => (
        <span className="font-mono text-label">
          <Highlight text={instance.instance_key} query={query} />
          {instance.managed_by ? (
            <span className="ml-1.5 font-sans text-caption text-text-tertiary">({MANAGED_BY_LABEL[instance.managed_by]})</span>
          ) : null}
        </span>
      ),
    },
    { id: "image", header: "Image", cell: (instance) => <span className="text-label text-text-secondary">{instance.image ?? "—"}</span> },
    { id: "sdk", header: "SDK", cell: (instance) => <span className="text-label text-text-secondary tabular-nums">{instance.sdk_version ?? "—"}</span> },
    { id: "status", header: "Status", cell: (instance) => <InstanceStatus instance={instance} /> },
    {
      id: "heartbeat",
      header: "Last heartbeat",
      cell: (instance) =>
        instance.last_heartbeat_at ? (
          <RelativeTime iso={instance.last_heartbeat_at} className="text-label text-text-secondary" />
        ) : (
          <span className="text-label text-text-tertiary">—</span>
        ),
    },
    {
      id: "providers",
      header: "Providers",
      align: "end",
      cell: (instance) => <span className="text-label text-text-secondary tabular-nums">{instance.installed_provider_ids?.length ?? 0}</span>,
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <WorkerStatusNotice connection={connection} showStart={false} />
      {health.mixedManagement ? (
        <Alert tone="warning" title="Two kinds of worker are registered">
          This pool has both <span className="font-mono">external</span> and <span className="font-mono">supervisor</span>{" "}
          workers. Stop the external one before relying on the supervised pool, or the two will compete for calls.
        </Alert>
      ) : null}

      <Section id="connection-pool" title="Worker pool" description="How many workers LKAP keeps running for this connection.">
        <SectionRow className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-stat-label text-text-secondary">Ready workers</span>
            <span className="text-stat font-semibold tracking-[-0.02em] text-foreground tabular-nums">
              {health.ready}/{health.desired}
            </span>
            {data?.restart_requested_at ? (
              <span className="text-caption text-text-secondary">
                Restart requested <RelativeTime iso={data.restart_requested_at} />
              </span>
            ) : null}
          </div>
          <IfCan min="admin" fallback={<ReadOnlyNote>{readOnlyCopy("admin", "start, stop or restart workers")}</ReadOnlyNote>}>
            <PoolControls connection={connection} desired={health.desired} />
          </IfCan>
        </SectionRow>
      </Section>

      <Section id="connection-instances" title="Instances" description="Workers running now, plus any that left in the last hour.">
        <SectionRow>
          {instances.length === 0 ? (
            <EmptyState
              variant="plain"
              icon={ServerIcon}
              title="No workers have registered yet"
              description="A worker shows up here a few seconds after it starts."
            />
          ) : (
            <>
              <ListSearchField search={search} label="Search instances" total={instances.length} />
              {search.noMatches ? (
                <ListNoMatches search={search} items="instances" />
              ) : (
                <ResponsiveTable<WorkerInstanceOut>
                  label="Worker instances"
                  columns={columns}
                  rows={search.filtered}
                  getRowKey={(instance) => instance.instance_key}
                  renderCard={(instance) => (
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-start justify-between gap-2">
                        <span className="min-w-0 truncate font-mono text-label">
                          <Highlight text={instance.instance_key} query={query} />
                        </span>
                        <InstanceStatus instance={instance} />
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-1 text-caption text-text-secondary">
                        <span>{instance.image ?? "—"}</span>
                        {instance.managed_by ? <span>{MANAGED_BY_LABEL[instance.managed_by]}</span> : null}
                        {instance.last_heartbeat_at ? (
                          <span>
                            Heartbeat <RelativeTime iso={instance.last_heartbeat_at} />
                          </span>
                        ) : null}
                      </div>
                    </div>
                  )}
                />
              )}
            </>
          )}
        </SectionRow>
      </Section>
    </div>
  );
}

function InstanceStatus({ instance }: { instance: WorkerInstanceOut }) {
  return (
    <StatusPill tone={instanceStatusTone(instance.status)} size="sm">
      {instanceStatusLabel(instance.status)}
    </StatusPill>
  );
}

/** Replicas stepper plus Start, Restart and Stop (confirmed). Admins only. */
function PoolControls({ connection, desired }: { connection: ConnectionOut; desired: number }) {
  const { data } = useConnectionFleet(connection.id, { poll: true });
  const fleetAction = useFleetAction(connection.id);
  const [replicas, setReplicas] = React.useState(connection.replicas ?? 1);
  const [pendingAction, setPendingAction] = React.useState<"start" | "restart" | null>(null);

  React.useEffect(() => {
    if (data?.desired_replicas !== undefined) setReplicas(data.desired_replicas || connection.replicas || 1);
  }, [data?.desired_replicas, connection.replicas]);

  async function act(action: "start" | "restart", nextReplicas?: number) {
    setPendingAction(action);
    try {
      await fleetAction.mutateAsync({ action, replicas: nextReplicas });
      toast.success(action === "restart" ? "Restart requested. The pool rolls over at the next check." : "Pool started.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1" role="group" aria-label="Desired replicas">
        <Button type="button" size="icon-sm" aria-label="Fewer replicas" onClick={() => setReplicas((r) => Math.max(0, r - 1))}>
          <MinusIcon aria-hidden="true" />
        </Button>
        <span className="w-8 text-center text-body text-foreground tabular-nums" aria-live="polite">
          {replicas}
        </span>
        <Button type="button" size="icon-sm" aria-label="More replicas" onClick={() => setReplicas((r) => r + 1)}>
          <PlusIcon aria-hidden="true" />
        </Button>
      </div>
      <Button
        type="button"
        disabled={fleetAction.isPending}
        busy={pendingAction === "start"}
        busyLabel="Starting…"
        onClick={() => void act("start", Math.max(1, replicas))}
      >
        Start
      </Button>
      <Button
        type="button"
        disabled={fleetAction.isPending || desired === 0}
        busy={pendingAction === "restart"}
        busyLabel="Restarting…"
        onClick={() => void act("restart")}
      >
        <RefreshCwIcon aria-hidden="true" />
        Restart
      </Button>
      <ConfirmDialog
        trigger={
          <Button type="button" variant="danger-outline" disabled={fleetAction.isPending || desired === 0}>
            Stop
          </Button>
        }
        title="Stop the worker pool?"
        description="Every worker for this connection stops, and agents bound to it stop answering calls until you start the pool again."
        confirmLabel="Stop pool"
        busyLabel="Stopping…"
        onConfirm={async () => {
          await fleetAction.mutateAsync({ action: "stop" });
          toast.success("Pool stopped.");
        }}
      />
    </div>
  );
}
