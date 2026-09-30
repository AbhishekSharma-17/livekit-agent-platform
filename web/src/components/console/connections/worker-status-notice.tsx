"use client";

import * as React from "react";
import { toast } from "sonner";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/shared/copy-button";
import { StatusPill } from "@/components/shared/status-chip";
import { lifecycleStatus } from "@/components/shared/status-map";
import {
  NO_WORKER_TITLE,
  OTHER_CONNECTION_WORKER_NOTE,
  WORKER_START_COMMAND,
  sharedAgentNameWarning,
  workerStatusLabel,
} from "@/components/console/connections/connection-model";
import { errorMessage } from "@/components/console/shared/error-banner";
import { IfCan, readOnlyCopy } from "@/components/console/shared/permission";
import { fetchWorkerEnv, useConnectionFleet, useFleetAction } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/**
 * V6-27: "which worker serves this connection", on the Overview and Fleet tabs.
 *
 * Reads `GET /v1/connections/{id}/fleet` (every mode; only the fleet *actions*
 * are supervised-only). With no ready worker it shows the empty state and the
 * next step for the connection's mode: External — copy the worker settings
 * (secrets stay `<…>` placeholders) and the start command; Supervised — Start
 * (hidden where the Fleet tab's own Start button is right below:
 * `showStart={false}`; admins only, since fleet actions are admin writes);
 * Cloud-hosted — the Deploy tab's bundle. Separately, it warns when another
 * connection's workers answer to the same agent name on the same LiveKit
 * server. Renders nothing until the counts are known.
 *
 * Worker readiness reads as a word plus a tone from the shared lifecycle map
 * (docs/ui/DESIGN-SYSTEM.md section 6.6).
 */
export function WorkerStatusNotice({
  connection,
  showStart = true,
}: {
  connection: ConnectionOut;
  showStart?: boolean;
}) {
  const { data } = useConnectionFleet(connection.id, { poll: true });
  const ready = data?.ready_workers;
  const shared = sharedAgentNameWarning(data?.shared_agent_name_workers, connection.agent_name ?? "lkap-agent");
  if (ready === undefined || ready === null) return null;

  return (
    <div className="flex flex-col gap-3" data-slot="worker-status">
      {ready > 0 ? (
        <div>
          <StatusPill tone={lifecycleStatus("ready").tone}>{workerStatusLabel(ready)}</StatusPill>
        </div>
      ) : (
        <Alert tone="warning" title={NO_WORKER_TITLE}>
          <div className="flex flex-col gap-3">
            <NextStep connection={connection} showStart={showStart} />
            <p>{OTHER_CONNECTION_WORKER_NOTE}</p>
          </div>
        </Alert>
      )}
      {shared ? <Alert tone="warning">{shared}</Alert> : null}
    </div>
  );
}

function NextStep({ connection, showStart }: { connection: ConnectionOut; showStart: boolean }) {
  switch (connection.deployment_mode ?? "external") {
    case "supervised":
      return <SupervisedStep connection={connection} showStart={showStart} />;
    case "cloud_hosted":
      return (
        <p>
          Deploy a worker to LiveKit Cloud with the bundle from the Deploy tab (Download deploy bundle). Agents bound
          here answer calls once it is running.
        </p>
      );
    default:
      return <ExternalStep connection={connection} />;
  }
}

function SupervisedStep({ connection, showStart }: { connection: ConnectionOut; showStart: boolean }) {
  const fleetAction = useFleetAction(connection.id);
  const replicas = Math.max(1, connection.replicas ?? 1);

  async function start() {
    try {
      await fleetAction.mutateAsync({ action: "start", replicas });
      toast.success("Starting workers for this connection.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (!showStart) {
    return <p>LKAP starts workers for this connection when you press Start below.</p>;
  }
  return (
    <IfCan min="admin" fallback={<p>{readOnlyCopy("admin", "start workers for this connection")}</p>}>
      <div className="flex flex-wrap items-center gap-3">
        <p>LKAP starts workers for this connection when you press Start.</p>
        <Button type="button" size="sm" onClick={() => void start()} busy={fleetAction.isPending} busyLabel="Starting…">
          Start
        </Button>
      </div>
    </IfCan>
  );
}

function ExternalStep({ connection }: { connection: ConnectionOut }) {
  const [pending, setPending] = React.useState(false);

  async function copySettings() {
    setPending(true);
    try {
      const text = await fetchWorkerEnv(connection.id, "env");
      await navigator.clipboard.writeText(text);
      toast.success("Worker settings copied. Fill in the <…> placeholders before you start it.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <p>
        You start the worker. Copy this connection&apos;s worker settings (secrets are{" "}
        <span className="font-mono">{"<…>"}</span> placeholders you fill in), save them outside the repository as{" "}
        <span className="font-mono">~/.config/lkap/worker.env</span>, then run this from the repository root:
      </p>
      <div className="flex items-start gap-1">
        <code className="min-w-0 flex-1 overflow-x-auto rounded-sm border border-border bg-card px-2 py-1 font-mono text-caption break-all text-foreground">
          {WORKER_START_COMMAND}
        </code>
        <CopyButton value={WORKER_START_COMMAND} label="Copy the start command" size="xs" />
      </div>
      <div>
        <Button type="button" size="sm" onClick={() => void copySettings()} busy={pending} busyLabel="Copying…">
          Copy worker settings
        </Button>
      </div>
    </div>
  );
}
