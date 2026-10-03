"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowRightIcon, FlaskConicalIcon, KeyRoundIcon, PlugIcon, StarIcon, Trash2Icon } from "lucide-react";

import { Icon, NewResourceButton, ResponsiveTable, RowMenu, StatusPill, Tag, VendorMark } from "@/components/shared";
import type { RowMenuAction } from "@/components/shared/row-menu";
import { EmptyState } from "@/components/shared/empty-state";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import {
  connectionStatusLabel,
  connectionStatusTone,
  DEPLOYMENT_MODE_LABEL,
  DEPLOYMENT_TYPE_LABEL,
  fleetHealth,
  urlHost,
} from "@/components/console/connections/connection-model";
import { DeleteConnectionDialog } from "@/components/console/connections/delete-connection-dialog";
import { RowsSkeleton } from "@/components/console/registry/rows-skeleton";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useCan } from "@/components/console/shared/permission";
import { useConnectionFleet, useConnections, useSetDefaultConnection, useTestConnection } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/** Admins add connections (`auth/roles.py::ROUTE_POLICY`: `/v1/connections` writes need `admin`). */
const READ_ONLY_NOTE = "Ask an admin to add connections.";

function typeLabel(connection: ConnectionOut): string {
  return DEPLOYMENT_TYPE_LABEL[connection.deployment_type ?? "cloud"];
}

function modeLabel(connection: ConnectionOut): string {
  return DEPLOYMENT_MODE_LABEL[connection.deployment_mode ?? "external"];
}

function ConnectionStatus({ connection }: { connection: ConnectionOut }) {
  return (
    <StatusPill tone={connectionStatusTone(connection.status)} size="sm">
      {connectionStatusLabel(connection.status)}
    </StatusPill>
  );
}

/**
 * `/console/connections` list (UI_UX_SPEC-V2-AMENDMENTS §2.1; docs/ui/DESIGN-SYSTEM.md
 * section 7.4 "List"): Name, Type, URL host, Status, Fleet, the default star
 * and a row menu (Open, Test, Make default, Rotate keys, Delete). Search
 * appears once there are 6 or more connections. Rotate opens the
 * connection's own page, where the two secret fields live.
 */
export function ConnectionsTable() {
  const { data, isLoading, isError, error, refetch } = useConnections();
  const connections = React.useMemo(() => data?.items ?? [], [data]);
  const search = useListSearch("connections", connections, (connection) => [
    connection.name,
    connection.slug,
    urlHost(connection.url),
    typeLabel(connection),
    modeLabel(connection),
    connectionStatusLabel(connection.status),
  ]);
  const query = search.query;

  if (isLoading) {
    return <RowsSkeleton label="Loading connections" />;
  }

  if (isError) {
    return <ErrorBanner error={error} context={{ action: "load connections" }} onRetry={() => void refetch()} />;
  }

  if (connections.length === 0) {
    return (
      <EmptyState
        icon={PlugIcon}
        title="No connections yet"
        description="Add a LiveKit Cloud project or your own LiveKit server so your agents have somewhere to run."
        action={
          <NewResourceButton href="/console/connections/new" min="admin" readOnlyNote={READ_ONLY_NOTE}>
            New connection
          </NewResourceButton>
        }
      />
    );
  }

  const columns: ResponsiveTableColumn<ConnectionOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (connection) => (
        <div className="flex min-w-0 items-center gap-1.5">
          {connection.is_default ? (
            <Icon as={StarIcon} size="sm" className="shrink-0 fill-current text-warning-text" label="Default connection" />
          ) : null}
          <div className="min-w-0">
            <p className="truncate font-medium text-foreground">
              <Highlight text={connection.name} query={query} />
            </p>
            <p className="truncate font-mono text-caption text-text-tertiary">
              <Highlight text={`/${connection.slug}`} query={query} />
            </p>
          </div>
        </div>
      ),
    },
    {
      id: "type",
      header: "Type",
      cell: (connection) => (
        <div className="flex flex-col items-start gap-1">
          <Tag>
            <VendorMark vendor="LiveKit" size="xs" className="-ml-1" />
            {typeLabel(connection)}
          </Tag>
          <span className="text-caption text-text-secondary">{modeLabel(connection)}</span>
        </div>
      ),
    },
    {
      id: "url",
      header: "URL",
      cell: (connection) => (
        <span className="font-mono text-label text-text-secondary">
          <Highlight text={urlHost(connection.url)} query={query} />
        </span>
      ),
    },
    { id: "status", header: "Status", cell: (connection) => <ConnectionStatus connection={connection} /> },
    {
      id: "fleet",
      header: "Fleet",
      cell: (connection) => <FleetCell connectionId={connection.id} deploymentMode={connection.deployment_mode} />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (connection) => <ConnectionRowMenu connection={connection} />,
    },
  ];

  return (
    <div className="flex flex-col">
      <ListSearchField search={search} label="Search connections" total={connections.length} />
      {search.noMatches ? (
        <ListNoMatches search={search} items="connections" />
      ) : (
        <ResponsiveTable<ConnectionOut>
          columns={columns}
          rows={search.filtered}
          label="Connections"
          getRowKey={(connection) => connection.id}
          rowHref={(connection) => `/console/connections/${connection.id}`}
          renderCard={(connection) => <ConnectionCard connection={connection} query={query} />}
        />
      )}
    </div>
  );
}

/** Only fires for `supervised` connections — `external`/`cloud_hosted` pools have no desired-state row to poll. */
function FleetCell({ connectionId, deploymentMode }: { connectionId: string; deploymentMode: ConnectionOut["deployment_mode"] }) {
  const { data } = useConnectionFleet(connectionId, { poll: false });
  if (deploymentMode !== "supervised") {
    return (
      <span className="text-label text-text-tertiary">Not managed here</span>
    );
  }
  const health = fleetHealth(data?.desired_replicas, data?.instances);
  return (
    <span className="text-label text-text-secondary tabular-nums">
      {health.ready}/{health.desired} ready
    </span>
  );
}

function ConnectionCard({ connection, query }: { connection: ConnectionOut; query: string }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 truncate font-medium text-foreground">
            {connection.is_default ? (
              <Icon as={StarIcon} size="sm" className="shrink-0 fill-current text-warning-text" label="Default" />
            ) : null}
            <Highlight text={connection.name} query={query} />
          </p>
          <p className="truncate font-mono text-caption text-text-tertiary">{urlHost(connection.url)}</p>
        </div>
        <ConnectionStatus connection={connection} />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-text-secondary">
        <span className="inline-flex items-center gap-1.5">
          <VendorMark vendor="LiveKit" size="xs" />
          {typeLabel(connection)}
        </span>
        <span aria-hidden="true">·</span>
        <span>{modeLabel(connection)}</span>
      </div>
      <div className="flex items-center justify-end pt-1">
        <ConnectionRowMenu connection={connection} />
      </div>
    </div>
  );
}

/**
 * The row "…" menu. Open is for everyone; Test, Make default, Rotate keys and
 * Delete are admin-only server-side (`auth/roles.py::ROUTE_POLICY`: every
 * `/v1/connections` write), so they are not offered to anyone else (D12).
 */
function ConnectionRowMenu({ connection }: { connection: ConnectionOut }) {
  const router = useRouter();
  const testConnection = useTestConnection();
  const setDefault = useSetDefaultConnection();
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const { can } = useCan("admin");

  async function runTest() {
    try {
      const result = await testConnection.mutateAsync(connection.id);
      if (result.ok) toast.success(`${connection.name} is working.`);
      else toast.error(`${connection.name} didn't pass the test. ${result.message}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function makeDefault() {
    try {
      await setDefault.mutateAsync(connection.id);
      toast.success(`${connection.name} is now the default connection.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const actions: RowMenuAction[] = [
    { label: "Open", icon: ArrowRightIcon, onSelect: () => router.push(`/console/connections/${connection.id}`) },
  ];
  if (can) {
    actions.push({
      label: testConnection.isPending ? "Testing…" : "Test",
      icon: FlaskConicalIcon,
      disabled: testConnection.isPending,
      onSelect: () => void runTest(),
    });
    if (!connection.is_default) {
      actions.push({ label: "Make default", icon: StarIcon, disabled: setDefault.isPending, onSelect: () => void makeDefault() });
    }
    actions.push({
      label: "Rotate keys",
      icon: KeyRoundIcon,
      onSelect: () => router.push(`/console/connections/${connection.id}?tab=overview#rotate`),
    });
  }

  return (
    <>
      <RowMenu
        label={`Actions for ${connection.name}`}
        size="sm"
        actions={actions}
        destructive={can ? { label: "Delete", icon: Trash2Icon, onSelect: () => setDeleteOpen(true) } : undefined}
      />
      {can ? <DeleteConnectionDialog connection={connection} open={deleteOpen} onOpenChange={setDeleteOpen} /> : null}
    </>
  );
}
