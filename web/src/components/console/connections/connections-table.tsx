"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { MoreHorizontalIcon, PlugIcon, StarIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState, Icon, ResponsiveTable, StatusChip } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import {
  connectionStatusLabel,
  connectionStatusTone,
  DEPLOYMENT_MODE_LABEL,
  DEPLOYMENT_TYPE_LABEL,
  fleetHealth,
  urlHost,
} from "@/components/console/connections/connection-model";
import { errorMessage } from "@/components/console/shared/error-banner";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import {
  useConnectionFleet,
  useConnections,
  useDeleteConnection,
  useSetDefaultConnection,
  useTestConnection,
} from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";
import { LoadingRegion } from "@/components/shared/loading-state";

/**
 * `/console/connections` list (UI_UX_SPEC-V2-AMENDMENTS §2.1): Name, Type,
 * URL host, Status, Capabilities, Fleet, Default star, and a row menu (Test,
 * Make default, Rotate keys, Delete). Rotate opens the connection's own
 * detail page (a modal here would need the secret fields WP-4's credential
 * sheet already builds for provider keys — connections keep the same two
 * secret fields inline on their own page instead of duplicating that UI).
 */
export function ConnectionsTable() {
  const { data, isLoading, isError, error, refetch } = useConnections();

  if (isLoading) {
    return (
      <LoadingRegion label="Loading connections" className="flex flex-col gap-2">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </LoadingRegion>
    );
  }

  if (isError) {
    return <ErrorBanner message={`Couldn't reach the api: ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const connections = data?.items ?? [];

  if (connections.length === 0) {
    return (
      <EmptyState
        icon={PlugIcon}
        title="No connections yet"
        description="A connection is a LiveKit Cloud project or self-hosted server your agents run on."
        action={
          <Button asChild>
            <Link href="/console/connections/new">New connection</Link>
          </Button>
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
            <Icon as={StarIcon} size="sm" className="shrink-0 fill-current text-warning" label="Default connection" />
          ) : null}
          <div className="min-w-0">
            <p className="truncate font-medium text-foreground">{connection.name}</p>
            <p className="truncate font-mono text-xs text-muted-foreground">/{connection.slug}</p>
          </div>
        </div>
      ),
    },
    {
      id: "type",
      header: "Type",
      cell: (connection) => (
        <div className="flex flex-col gap-1">
          <StatusChip tone={connection.deployment_type === "cloud" ? "info" : "neutral"} size="sm">
            {DEPLOYMENT_TYPE_LABEL[connection.deployment_type ?? "cloud"]}
          </StatusChip>
          <span className="text-xs text-muted-foreground">
            {DEPLOYMENT_MODE_LABEL[connection.deployment_mode ?? "external"]}
          </span>
        </div>
      ),
    },
    {
      id: "url",
      header: "URL",
      cell: (connection) => <span className="font-mono text-[0.8125rem] text-muted-foreground">{urlHost(connection.url)}</span>,
    },
    {
      id: "status",
      header: "Status",
      cell: (connection) => (
        <StatusChip tone={connectionStatusTone(connection.status)} dot size="sm">
          {connectionStatusLabel(connection.status)}
        </StatusChip>
      ),
    },
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
    <ResponsiveTable<ConnectionOut>
      columns={columns}
      rows={connections}
      label="Connections"
      getRowKey={(connection) => connection.id}
      rowHref={(connection) => `/console/connections/${connection.id}`}
      renderCard={(connection) => <ConnectionCard connection={connection} />}
    />
  );
}

/** Only fires for `supervised` connections — `external`/`cloud_hosted` pools have no desired-state row to poll. */
function FleetCell({ connectionId, deploymentMode }: { connectionId: string; deploymentMode: ConnectionOut["deployment_mode"] }) {
  const { data } = useConnectionFleet(connectionId, { poll: false });
  if (deploymentMode !== "supervised") {
    return <span className="text-[0.8125rem] text-muted-foreground">—</span>;
  }
  const health = fleetHealth(data?.desired_replicas, data?.instances);
  return (
    <span className="text-[0.8125rem] tabular-nums text-muted-foreground">
      {health.ready}/{health.desired} ready
    </span>
  );
}

function ConnectionCard({ connection }: { connection: ConnectionOut }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 truncate font-medium text-foreground">
            {connection.is_default ? <Icon as={StarIcon} size="sm" className="shrink-0 fill-current text-warning" label="Default" /> : null}
            {connection.name}
          </p>
          <p className="truncate font-mono text-xs text-muted-foreground">{urlHost(connection.url)}</p>
        </div>
        <StatusChip tone={connectionStatusTone(connection.status)} dot size="sm">
          {connectionStatusLabel(connection.status)}
        </StatusChip>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{DEPLOYMENT_TYPE_LABEL[connection.deployment_type ?? "cloud"]}</span>
        <span aria-hidden="true">·</span>
        <span>{DEPLOYMENT_MODE_LABEL[connection.deployment_mode ?? "external"]}</span>
      </div>
      <div className="flex items-center justify-end pt-1">
        <ConnectionRowMenu connection={connection} />
      </div>
    </div>
  );
}

type RowConfirmAction = "delete" | null;

function ConnectionRowMenu({ connection }: { connection: ConnectionOut }) {
  const testConnection = useTestConnection();
  const setDefault = useSetDefaultConnection();
  const deleteConnection = useDeleteConnection();
  const [confirmAction, setConfirmAction] = React.useState<RowConfirmAction>(null);

  async function runTest() {
    try {
      const result = await testConnection.mutateAsync(connection.id);
      toast[result.ok ? "success" : "error"](result.ok ? `${connection.name}: connection OK.` : `${connection.name}: ${result.message}`);
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

  async function confirmDelete() {
    try {
      await deleteConnection.mutateAsync(connection.id);
      toast.success(`${connection.name} deleted.`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setConfirmAction(null);
    }
  }

  return (
    <Dialog open={confirmAction !== null} onOpenChange={(open) => !open && setConfirmAction(null)}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" aria-label={`Actions for ${connection.name}`}>
            <Icon as={MoreHorizontalIcon} size="md" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem asChild>
            <Link href={`/console/connections/${connection.id}`}>Open</Link>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void runTest()} disabled={testConnection.isPending}>
            {testConnection.isPending ? "Testing…" : "Test"}
          </DropdownMenuItem>
          {!connection.is_default ? (
            <DropdownMenuItem onSelect={() => void makeDefault()} disabled={setDefault.isPending}>
              Make default
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem asChild>
            <Link href={`/console/connections/${connection.id}?tab=overview#rotate`}>Rotate keys</Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => setConfirmAction("delete")}>
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete &quot;{connection.name}&quot;?</DialogTitle>
          <DialogDescription>
            Connections with agents bound to them can&apos;t be deleted — unbind those agents first.
            {connection.is_default ? " The default connection can't be deleted either — make another one the default first." : null}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setConfirmAction(null)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            disabled={deleteConnection.isPending}
            onClick={() => void confirmDelete()}
          >
            {deleteConnection.isPending ? "Working…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
