"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { CapabilityList } from "@/components/console/connections/capability-list";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AGENT_NAME_HINT,
  agentNameError,
  connectionStatusLabel,
  connectionStatusTone,
  DEPLOYMENT_MODE_LABEL,
  DEPLOYMENT_TYPE_LABEL,
} from "@/components/console/connections/connection-model";
import { RotateDialog } from "@/components/console/connections/rotate-dialog";
import { WorkerStatusNotice } from "@/components/console/connections/worker-status-notice";
import { INFERENCE_CREDITS_LINE } from "@/components/console/registry/provider-meta";
import { errorMessage } from "@/components/console/shared/error-banner";
import { CopyButton } from "@/components/shared/copy-button";
import { DescriptionList } from "@/components/shared/description-list";
import { Field } from "@/components/shared/field";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip } from "@/components/shared/status-chip";
import { useSetDefaultConnection, useTestConnection, useUpdateConnection } from "@/hooks/useConnections";
import type { ConnectionOut, ConnectionUpdate } from "@/contracts/lkap-contracts";

/** Detail → Overview tab: facts, test, rotate, make default, and a plain edit of the non-secret fields (secrets change only through Rotate). */
export function ConnectionOverview({ connection }: { connection: ConnectionOut }) {
  const testConnection = useTestConnection();
  const setDefault = useSetDefaultConnection();
  const [editing, setEditing] = React.useState(false);
  const [testWarnings, setTestWarnings] = React.useState<string[]>([]);

  async function runTest() {
    try {
      const result = await testConnection.mutateAsync(connection.id);
      // V6-27: never a failure — the agent-name findings stay on the page until the next test.
      setTestWarnings(result.warnings ?? []);
      if (!result.ok) toast.error(result.message);
      else toast.success("Connection OK.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusChip tone={connectionStatusTone(connection.status)} dot>
          {connectionStatusLabel(connection.status)}
        </StatusChip>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={() => void runTest()} disabled={testConnection.isPending}>
            {testConnection.isPending ? "Testing…" : "Test connection"}
          </Button>
          {!connection.is_default ? (
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                setDefault.mutate(connection.id, {
                  onSuccess: () => toast.success(`${connection.name} is now the default connection.`),
                  onError: (error) => toast.error(errorMessage(error)),
                })
              }
              disabled={setDefault.isPending}
            >
              Make default
            </Button>
          ) : null}
          <RotateDialog connection={connection} />
        </div>
      </div>

      {testWarnings.length > 0 ? (
        <Alert variant="warning">
          <AlertDescription>
            <ul className="flex list-none flex-col gap-1 p-0">
              {testWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <WorkerStatusNotice connection={connection} />

      <DescriptionList
        columns={2}
        items={[
          { term: "Type", detail: DEPLOYMENT_TYPE_LABEL[connection.deployment_type ?? "cloud"] },
          { term: "Deployment mode", detail: DEPLOYMENT_MODE_LABEL[connection.deployment_mode ?? "external"] },
          { term: "URL", detail: connection.url, mono: true },
          {
            term: "Fingerprint",
            detail: (
              <span className="inline-flex items-center gap-1">
                <span className="font-mono">{connection.fingerprint ?? "—"}</span>
                {connection.fingerprint ? <CopyButton value={connection.fingerprint} label="Copy fingerprint" size="xs" /> : null}
              </span>
            ),
          },
          { term: "Agent name", detail: connection.agent_name ?? "lkap-agent", mono: true },
          { term: "Use LiveKit Inference", detail: connection.use_inference ? "Yes" : "No" },
          { term: "Worker image", detail: connection.worker_image ?? "slim" },
          { term: "Replicas (supervised)", detail: String(connection.replicas ?? 1) },
          {
            term: "Last checked",
            detail: connection.last_checked_at ? <RelativeTime iso={connection.last_checked_at} /> : "Never",
          },
        ]}
      />

      {(connection.deployment_type ?? "cloud") === "cloud" ? (
        <p className="text-[0.8125rem] text-pretty text-muted-foreground">
          {INFERENCE_CREDITS_LINE.text} <span className="text-xs">(as of {INFERENCE_CREDITS_LINE.asOf})</span>
        </p>
      ) : null}

      {connection.last_error ? (
        <p className="rounded-md bg-danger-soft px-3 py-2 text-[0.8125rem] text-pretty text-danger-text">
          {connection.last_error}
        </p>
      ) : null}

      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-foreground">Capabilities</h2>
        <CapabilityList capabilities={connection.capabilities} />
      </div>

      <div className="flex flex-col gap-3 border-t border-border pt-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">Settings</h2>
          {!editing ? (
            <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(true)}>
              Edit
            </Button>
          ) : null}
        </div>
        {editing ? (
          <EditForm connection={connection} onDone={() => setEditing(false)} />
        ) : null}
      </div>
    </div>
  );
}

function EditForm({ connection, onDone }: { connection: ConnectionOut; onDone: () => void }) {
  const update = useUpdateConnection(connection.id);
  const [name, setName] = React.useState(connection.name);
  const [agentName, setAgentName] = React.useState(connection.agent_name ?? "lkap-agent");
  const [useInference, setUseInference] = React.useState(connection.use_inference ?? true);
  const [replicas, setReplicas] = React.useState(connection.replicas ?? 1);
  const [agentNameProblem, setAgentNameProblem] = React.useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const body: ConnectionUpdate = {
      name: name.trim(),
      agent_name: agentName.trim() || null,
      use_inference: connection.deployment_type === "cloud" ? useInference : false,
      replicas,
    };
    try {
      await update.mutateAsync(body);
      toast.success("Connection updated.");
      onDone();
    } catch (error) {
      // V6-27: an agent-name clash belongs under the field, not only in a toast.
      const inline = agentNameError(error);
      if (inline) setAgentNameProblem(inline);
      else toast.error(errorMessage(error));
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <Field label="Name" htmlFor="edit-conn-name">
        <Input id="edit-conn-name" value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="Agent name" htmlFor="edit-conn-agent-name" hint={AGENT_NAME_HINT} error={agentNameProblem}>
        <Input
          id="edit-conn-agent-name"
          value={agentName}
          onChange={(event) => {
            setAgentName(event.target.value);
            setAgentNameProblem(null);
          }}
          className="font-mono text-[0.8125rem]"
        />
      </Field>
      {connection.deployment_type === "cloud" ? (
        <Field label="Use LiveKit Inference" htmlFor="edit-conn-inference" inline>
          <Switch id="edit-conn-inference" checked={useInference} onCheckedChange={setUseInference} />
        </Field>
      ) : null}
      <Field label="Replicas" htmlFor="edit-conn-replicas" hint="Used when the pool is supervised.">
        <Input
          id="edit-conn-replicas"
          type="number"
          min={1}
          value={replicas}
          onChange={(event) => setReplicas(Math.max(1, Number(event.target.value) || 1))}
          className="w-24 tabular-nums"
        />
      </Field>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}
