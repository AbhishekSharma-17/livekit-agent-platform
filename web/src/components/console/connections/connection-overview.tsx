"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { PencilIcon, StarIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { CapabilityList } from "@/components/console/connections/capability-list";
import {
  AGENT_NAME_HINT,
  agentNameError,
  DEPLOYMENT_MODE_LABEL,
  DEPLOYMENT_TYPE_LABEL,
} from "@/components/console/connections/connection-model";
import { DeleteConnectionDialog } from "@/components/console/connections/delete-connection-dialog";
import { RotateDialog } from "@/components/console/connections/rotate-dialog";
import { WorkerStatusNotice } from "@/components/console/connections/worker-status-notice";
import { INFERENCE_CREDITS_LINE } from "@/components/console/registry/provider-meta";
import { errorMessage } from "@/components/console/shared/error-banner";
import { IfCan, ReadOnlyNote, readOnlyCopy } from "@/components/console/shared/permission";
import { CopyButton } from "@/components/shared/copy-button";
import { MetaList } from "@/components/shared/data-display";
import { DescriptionList } from "@/components/shared/description-list";
import { Field } from "@/components/shared/field";
import { RelativeTime } from "@/components/shared/relative-time";
import { Section, SectionRow } from "@/components/shared/section";
import { useSetDefaultConnection, useUpdateConnection } from "@/hooks/useConnections";
import type { ConnectionOut, ConnectionUpdate } from "@/contracts/lkap-contracts";
import { EMPTY_VALUE } from "@/lib/format";

/**
 * Detail → Overview tab, laid out as the Detail / record archetype
 * (docs/ui/DESIGN-SYSTEM.md section 7.4): a main column of work cards
 * (worker status, capabilities, settings) and a side column of facts
 * (details, keys) ending in a Danger zone with a typed confirmation. The side
 * column stacks under the main one on narrow screens. Every change is
 * admin-only server-side, so only admins are offered one (D12); secrets change
 * only through Rotate keys and are never shown.
 */
export function ConnectionOverview({ connection }: { connection: ConnectionOut }) {
  const router = useRouter();
  const [editing, setEditing] = React.useState(false);
  const isCloud = (connection.deployment_type ?? "cloud") === "cloud";

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,360px)]">
      <div className="flex min-w-0 flex-col gap-6">
        <WorkerStatusNotice connection={connection} />

        <Section
          id="connection-capabilities"
          title="Capabilities"
          description="What this LiveKit project offered at the last test."
        >
          <SectionRow>
            <CapabilityList capabilities={connection.capabilities} />
          </SectionRow>
          {isCloud ? (
            <SectionRow compact className="text-caption text-pretty text-text-secondary">
              {INFERENCE_CREDITS_LINE.text} <span>(as of {INFERENCE_CREDITS_LINE.asOf})</span>
            </SectionRow>
          ) : null}
        </Section>

        <Section
          id="connection-settings"
          title="Settings"
          description="The name, agent name, LiveKit Inference and supervised replicas."
          aside={
            !editing ? (
              <IfCan min="admin">
                <Button type="button" size="sm" onClick={() => setEditing(true)}>
                  <PencilIcon aria-hidden="true" />
                  Edit
                </Button>
              </IfCan>
            ) : null
          }
        >
          <SectionRow>
            {editing ? (
              <EditForm connection={connection} onDone={() => setEditing(false)} />
            ) : (
              <MetaList
                items={[
                  { term: "Name", value: connection.name },
                  { term: "Agent name", value: <span className="font-mono">{connection.agent_name ?? "lkap-agent"}</span> },
                  ...(isCloud
                    ? [{ term: "Use LiveKit Inference", value: connection.use_inference ? "Yes" : "No" }]
                    : []),
                  { term: "Replicas (supervised)", value: String(connection.replicas ?? 1) },
                ]}
              />
            )}
          </SectionRow>
        </Section>
      </div>

      <div className="flex min-w-0 flex-col gap-6">
        <Section id="connection-details" title="Details">
          <SectionRow>
            <DescriptionList
              items={[
                { term: "Type", detail: DEPLOYMENT_TYPE_LABEL[connection.deployment_type ?? "cloud"] },
                { term: "Deployment mode", detail: DEPLOYMENT_MODE_LABEL[connection.deployment_mode ?? "external"] },
                { term: "URL", detail: connection.url, mono: true },
                {
                  term: "Fingerprint",
                  detail: (
                    <span className="inline-flex items-center gap-1">
                      <span className="font-mono text-caption">{connection.fingerprint ?? EMPTY_VALUE}</span>
                      {connection.fingerprint ? <CopyButton value={connection.fingerprint} label="Copy fingerprint" size="xs" /> : null}
                    </span>
                  ),
                },
                { term: "Worker image", detail: connection.worker_image === "full" ? "Full" : "Slim" },
                {
                  term: "Last checked",
                  detail: connection.last_checked_at ? <RelativeTime iso={connection.last_checked_at} /> : "Never",
                },
              ]}
            />
          </SectionRow>
          <SectionRow compact className="flex flex-wrap items-center justify-between gap-2">
            {connection.is_default ? (
              <p className="flex items-center gap-1.5 text-label text-text-secondary">
                <StarIcon aria-hidden="true" className="size-3.5 fill-current text-warning-text" />
                The workspace&apos;s default connection.
              </p>
            ) : (
              <>
                <p className="text-label text-text-secondary">Not the default connection.</p>
                <IfCan min="admin">
                  <MakeDefaultButton connection={connection} />
                </IfCan>
              </>
            )}
          </SectionRow>
        </Section>

        <Section
          id="connection-keys"
          title="Keys"
          description="The LiveKit API key and secret are stored encrypted and never shown again."
        >
          <SectionRow>
            <IfCan min="admin" fallback={<ReadOnlyNote>{readOnlyCopy("admin", "rotate this connection's keys")}</ReadOnlyNote>}>
              <RotateDialog connection={connection} />
            </IfCan>
          </SectionRow>
        </Section>

        <Section id="connection-danger" title="Danger zone" description="Deleting a connection can't be undone.">
          <SectionRow>
            <IfCan
              min="admin"
              fallback={<ReadOnlyNote variant="block">{readOnlyCopy("admin", "delete this connection")}</ReadOnlyNote>}
            >
              <DeleteConnectionDialog
                connection={connection}
                onDeleted={() => router.push("/console/connections")}
                trigger={
                  <Button type="button" variant="danger-outline">
                    <Trash2Icon aria-hidden="true" />
                    Delete connection
                  </Button>
                }
              />
            </IfCan>
          </SectionRow>
        </Section>
      </div>
    </div>
  );
}

function MakeDefaultButton({ connection }: { connection: ConnectionOut }) {
  const setDefault = useSetDefaultConnection();
  return (
    <Button
      type="button"
      size="sm"
      busy={setDefault.isPending}
      busyLabel="Making default…"
      onClick={() =>
        setDefault.mutate(connection.id, {
          onSuccess: () => toast.success(`${connection.name} is now the default connection.`),
          onError: (error) => toast.error(errorMessage(error)),
        })
      }
    >
      Make default
    </Button>
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
          className="font-mono"
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
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" busy={update.isPending} busyLabel="Saving…">
          Save
        </Button>
      </div>
    </form>
  );
}
