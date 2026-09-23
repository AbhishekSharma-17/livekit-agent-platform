"use client";

import * as React from "react";
import { toast } from "sonner";
import { MinusIcon, PlusIcon, RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip } from "@/components/shared/status-chip";
import {
  fleetHealth,
  instanceStatusTone,
  visibleInstances,
} from "@/components/console/connections/connection-model";
import { errorMessage } from "@/components/console/shared/error-banner";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useConnectionFleet, useFleetAction } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/**
 * The Fleet tab (UI_UX_SPEC-V2-AMENDMENTS §2.1 "Detail → Fleet tab";
 * docs/v2/_asks.md #48, V2-04's hand-off): desired replicas stepper,
 * Start/Stop/Restart, and the instances table. Supervised connections only —
 * external/cloud-hosted pools have nothing here to manage (V2-04's fleet
 * routes 409 for them).
 */
export function FleetCard({ connection }: { connection: ConnectionOut }) {
  const supervised = connection.deployment_mode === "supervised";
  const { data, isLoading, isError, error, refetch } = useConnectionFleet(connection.id, { poll: true });
  const fleetAction = useFleetAction(connection.id);
  const [replicas, setReplicas] = React.useState(connection.replicas ?? 1);

  React.useEffect(() => {
    if (data?.desired_replicas !== undefined) setReplicas(data.desired_replicas || connection.replicas || 1);
  }, [data?.desired_replicas, connection.replicas]);

  if (!supervised) {
    return (
      <Alert>
        <AlertDescription>
          This connection is {connection.deployment_mode === "cloud_hosted" ? "cloud-hosted" : "external"} — the
          worker pool isn&apos;t managed here. {connection.deployment_mode === "external" ? "See the Deploy tab for the environment an external worker needs." : "See the Deploy tab for the deploy bundle."}
        </AlertDescription>
      </Alert>
    );
  }

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading fleet status…</p>;
  }
  if (isError) {
    return <ErrorBanner message={`Couldn't load the fleet — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const health = fleetHealth(data?.desired_replicas, data?.instances);
  const instances = visibleInstances(data?.instances);

  async function act(action: "start" | "stop" | "restart", nextReplicas?: number) {
    try {
      await fleetAction.mutateAsync({ action, replicas: nextReplicas });
      toast.success(
        action === "restart" ? "Restart requested — the pool rolls over the next reconcile." : `Pool ${action === "start" ? "started" : "stopped"}.`,
      );
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {health.mixedManagement ? (
        <Alert variant="warning">
          <AlertDescription>
            This pool has both <span className="font-mono">external</span> and{" "}
            <span className="font-mono">supervisor</span> workers registered — stop the external one before relying on
            the supervised pool (PLAN-V2 §7).
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-end justify-between gap-4 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted-foreground">Pool health</span>
          <span className="text-lg font-semibold tabular-nums text-foreground">
            {health.ready}/{health.desired} ready
          </span>
          {data?.restart_requested_at ? (
            <span className="text-xs text-muted-foreground">
              Restart requested <RelativeTime iso={data.restart_requested_at} />
            </span>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1">
            <Button type="button" variant="outline" size="icon-sm" aria-label="Fewer replicas" onClick={() => setReplicas((r) => Math.max(0, r - 1))}>
              <MinusIcon aria-hidden="true" />
            </Button>
            <span className="w-8 text-center text-sm tabular-nums text-foreground">{replicas}</span>
            <Button type="button" variant="outline" size="icon-sm" aria-label="More replicas" onClick={() => setReplicas((r) => r + 1)}>
              <PlusIcon aria-hidden="true" />
            </Button>
          </div>
          <Button type="button" variant="outline" disabled={fleetAction.isPending} onClick={() => void act("start", Math.max(1, replicas))}>
            Start
          </Button>
          <Button type="button" variant="outline" disabled={fleetAction.isPending || health.desired === 0} onClick={() => void act("stop")}>
            Stop
          </Button>
          <Button type="button" variant="outline" disabled={fleetAction.isPending || health.desired === 0} onClick={() => void act("restart")}>
            <RotateCwIcon aria-hidden="true" />
            Restart
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-foreground">Instances</h2>
        {instances.length === 0 ? (
          <p className="text-sm text-muted-foreground">No workers have registered yet.</p>
        ) : (
          <Table aria-label="Worker instances">
            <TableHeader>
              <TableRow>
                <TableHead>Instance</TableHead>
                <TableHead>Image</TableHead>
                <TableHead>SDK</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Last heartbeat</TableHead>
                <TableHead>Providers</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {instances.map((instance) => (
                <TableRow key={instance.instance_key}>
                  <TableCell className="font-mono text-[0.8125rem]">
                    {instance.instance_key}
                    {instance.managed_by ? <span className="ml-1.5 text-xs text-muted-foreground">({instance.managed_by})</span> : null}
                  </TableCell>
                  <TableCell className="text-[0.8125rem] text-muted-foreground">{instance.image ?? "—"}</TableCell>
                  <TableCell className="text-[0.8125rem] text-muted-foreground">{instance.sdk_version ?? "—"}</TableCell>
                  <TableCell>
                    <StatusChip tone={instanceStatusTone(instance.status)} size="sm" dot>
                      {instance.status ?? "unknown"}
                    </StatusChip>
                  </TableCell>
                  <TableCell className="text-[0.8125rem] text-muted-foreground">
                    {instance.last_heartbeat_at ? <RelativeTime iso={instance.last_heartbeat_at} /> : "—"}
                  </TableCell>
                  <TableCell className="text-[0.8125rem] text-muted-foreground">
                    {instance.installed_provider_ids?.length ?? 0}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
