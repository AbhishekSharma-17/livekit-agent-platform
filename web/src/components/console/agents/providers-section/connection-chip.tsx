"use client";

import * as React from "react";
import Link from "next/link";
import { useFormContext } from "react-hook-form";
import { CheckIcon, ChevronDownIcon, PlugIcon, TriangleAlertIcon } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Icon } from "@/components/shared/icon";
import { connectionTypeLabel } from "@/components/console/agents/editor/use-connection";
import { useProviders } from "@/components/console/lib/api-hooks";
import { connectionSwitchWarnings } from "@/components/console/agents/providers-section/connection-gate";
import { workerStatusLabel } from "@/components/console/connections/connection-model";
import { useConnections } from "@/hooks/useConnections";
import { cn } from "@/lib/utils";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, ConnectionOut } from "@/contracts/lkap-contracts";

/** V6-27: `true` only when the api reported zero ready workers (unknown is not zero). */
function hasNoWorkers(connection: ConnectionOut | undefined): boolean {
  return connection?.ready_workers === 0;
}

const CHIP =
  "inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-sm border border-border bg-card px-2 text-xs font-medium text-text-secondary outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring";

/**
 * The `connectionChip` slot (V2-13, `agents/editor/README.md`): the header's
 * read-only chip becomes a popover that changes `connection_id` (already a
 * form field). Warns — but does not block — when the pick would leave a
 * configured slot unusable (`connectionSwitchWarnings`); the providers
 * section's own slot cards are the enforcement (`disabledReason`), this is
 * just an early heads-up before the user scrolls down.
 *
 * V6-27: each option shows its connection's worker status ("2 workers ready" /
 * "No workers running"), and while the agent is bound to a connection with no
 * ready worker a non-blocking warning next to the chip links to that
 * connection's page, where the next step is.
 */
export function ConnectionChip({ agent: _agent }: { agent: AgentOut }) {
  const { watch, setValue } = useFormContext<AgentEditorForm>();
  const connectionId = watch("connection_id");
  const pipeline = watch("config.pipeline");
  const [open, setOpen] = React.useState(false);

  const { data: connectionsData } = useConnections();
  const { data: providersData } = useProviders();
  const connections = connectionsData?.items ?? [];
  const providers = providersData?.providers ?? [];

  const defaultConnection = connections.find((c) => c.is_default) ?? connections[0];
  const current = connectionId ? connections.find((c) => c.id === connectionId) : defaultConnection;

  const label = current
    ? `${current.name}${!connectionId ? " (default)" : ""} · ${connectionTypeLabel(current)}`
    : connectionId
      ? "Connection…"
      : "Default connection";

  function choose(nextId: string | null) {
    setValue("connection_id", nextId, { shouldDirty: true });
    setOpen(false);
  }

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button type="button" className={CHIP} data-slot="connection-chip">
            <Icon as={PlugIcon} size="sm" />
            <span className="sr-only">Connection: </span>
            <span className="truncate">{label}</span>
            <Icon as={ChevronDownIcon} size="sm" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80" aria-label="Choose a connection">
          <p className="mb-2 text-xs font-medium text-text-secondary">Run this agent on</p>
          <div className="flex flex-col gap-1" role="listbox" aria-label="Connections">
            <ConnectionOption
              label={`Workspace default${defaultConnection ? ` (${defaultConnection.name})` : ""}`}
              status={workerStatusLabel(defaultConnection?.ready_workers)}
              idle={hasNoWorkers(defaultConnection)}
              active={!connectionId}
              warnings={defaultConnection ? connectionSwitchWarnings(pipeline, providers, defaultConnection) : []}
              onSelect={() => choose(null)}
            />
            {connections.map((connection) => (
              <ConnectionOption
                key={connection.id}
                label={`${connection.name} · ${connectionTypeLabel(connection)}`}
                status={workerStatusLabel(connection.ready_workers)}
                idle={hasNoWorkers(connection)}
                active={connectionId === connection.id}
                warnings={connectionId === connection.id ? [] : connectionSwitchWarnings(pipeline, providers, connection)}
                onSelect={() => choose(connection.id)}
              />
            ))}
            {connections.length === 0 ? <p className="px-2 py-1.5 text-xs text-text-secondary">No connections yet.</p> : null}
          </div>
        </PopoverContent>
      </Popover>
      {current && hasNoWorkers(current) ? (
        <Link
          href={`/console/connections/${current.id}`}
          data-slot="connection-no-worker"
          className="inline-flex min-w-0 items-center gap-1 rounded-sm text-xs font-medium text-warning-text underline-offset-3 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon as={TriangleAlertIcon} size="sm" />
          <span className="truncate">No worker is running for “{current.name}”. Calls won&apos;t be answered. Start one</span>
        </Link>
      ) : null}
    </>
  );
}

function ConnectionOption({
  label,
  status,
  idle,
  active,
  warnings,
  onSelect,
}: {
  label: string;
  /** V6-27: `workerStatusLabel` of the connection, or `null` when unknown. */
  status: string | null;
  /** V6-27: the api reported zero ready workers. */
  idle: boolean;
  active: boolean;
  warnings: string[];
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onClick={onSelect}
      className={cn(
        "flex flex-col gap-0.5 rounded px-2 py-1.5 text-left text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
        active && "bg-brand-subtle",
      )}
    >
      <span className="flex items-center gap-1.5 text-foreground">
        {active ? <Icon as={CheckIcon} size="sm" /> : <span className="size-3.5 shrink-0" aria-hidden="true" />}
        {label}
      </span>
      {status ? (
        <span className={cn("pl-5 text-xs", idle ? "text-warning-text" : "text-text-secondary")}>{status}</span>
      ) : null}
      {warnings.length > 0 ? (
        <span className="pl-5 text-xs text-pretty text-warning-text">{warnings.join(" ")}</span>
      ) : null}
    </button>
  );
}
