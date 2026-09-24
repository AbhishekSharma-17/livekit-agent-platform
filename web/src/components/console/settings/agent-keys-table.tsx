"use client";

import * as React from "react";
import { BotIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusChip } from "@/components/shared/status-chip";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { api } from "@/lib/api";
import { SkeletonRows } from "@/components/shared/loading-state";

import { keyStatus } from "./api-keys-tab";
import type { ApiKeyOut } from "./api-types";
import { AGENT_KEY_CLIENTS } from "./snippets";
import { useAgentKeys, useActiveWorkspace, useInvalidateSettings } from "./use-settings-queries";

const CLIENT_LABEL: Record<string, string> = Object.fromEntries(AGENT_KEY_CLIENTS.map((c) => [c.id, c.label]));

function clientLabel(client: string | null): string {
  if (!client) return "—";
  return CLIENT_LABEL[client] ?? client;
}

/**
 * "Agent keys" (docs/v3/AGENT-ACCESS.md §5 item 2): every `kind=agent` key,
 * newest first, with revoke behind a `ConfirmDialog` (R-V3-2: dialogs only).
 * Revoked keys stay listed — the row loses its Revoke action, not itself.
 */
export function AgentKeysTable() {
  const { workspace } = useActiveWorkspace();
  const keysQuery = useAgentKeys(workspace?.id);
  const invalidate = useInvalidateSettings();
  const keys = keysQuery.data?.items ?? [];

  const columns: ResponsiveTableColumn<ApiKeyOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (key) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{key.name}</div>
          <div className="truncate font-mono text-xs text-muted-foreground">{key.prefix}…</div>
        </div>
      ),
    },
    {
      id: "client",
      header: "Client",
      cell: (key) => <StatusChip tone="neutral" size="sm">{clientLabel(key.client)}</StatusChip>,
    },
    {
      id: "scopes",
      header: "Scopes",
      cell: (key) => (
        <div className="flex flex-wrap gap-1">
          {key.scopes.map((scope) => (
            <span
              key={scope}
              className={
                scope === "calls:write"
                  ? "rounded-xs bg-warning-soft px-1.5 py-0.5 font-mono text-[0.6875rem] text-warning-text"
                  : "rounded-xs bg-muted px-1.5 py-0.5 font-mono text-[0.6875rem] text-muted-foreground"
              }
            >
              {scope}
            </span>
          ))}
        </div>
      ),
    },
    {
      id: "created",
      header: "Created",
      cell: (key) => <RelativeTime iso={key.created_at} />,
    },
    {
      id: "last_used",
      header: "Last used",
      cell: (key) => (key.last_used_at ? <RelativeTime iso={key.last_used_at} /> : <span className="text-muted-foreground">Never</span>),
    },
    {
      id: "last_client",
      header: "Last client",
      cell: (key) => <span className="text-xs text-muted-foreground">{clientLabel(key.last_client)}</span>,
    },
    {
      id: "expires",
      header: "Expires",
      cell: (key) => (key.expires_at ? <RelativeTime iso={key.expires_at} /> : <span className="text-muted-foreground">Never</span>),
    },
    {
      id: "status",
      header: "Status",
      cell: (key) => {
        const status = keyStatus(key);
        return (
          <StatusChip tone={status.tone} size="sm">
            {status.label}
          </StatusChip>
        );
      },
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (key) => (key.revoked_at ? null : <RevokeAgentKeyButton apiKey={key} onRevoked={() => invalidate(workspace?.id)} />),
    },
  ];

  if (keysQuery.isLoading) {
    return <SkeletonRows label="Loading agent keys" rowClassName="h-12" />;
  }
  if (keysQuery.isError) {
    return <ErrorBanner message={`Couldn't load agent keys — ${errorMessage(keysQuery.error)}`} onRetry={() => keysQuery.refetch()} />;
  }
  if (keys.length === 0) {
    return <EmptyState icon={BotIcon} title="No agent keys yet" description="Connect an AI agent above to mint the first one." compact />;
  }

  return (
    <ResponsiveTable<ApiKeyOut>
      columns={columns}
      rows={keys}
      label="Agent keys"
      getRowKey={(key) => key.id}
      renderCard={(key) => {
        const status = keyStatus(key);
        return (
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate font-medium text-foreground">{key.name}</div>
              <div className="text-xs text-muted-foreground">
                {clientLabel(key.client)} · {key.prefix}…
              </div>
            </div>
            <StatusChip tone={status.tone} size="sm">
              {status.label}
            </StatusChip>
          </div>
        );
      }}
    />
  );
}

function RevokeAgentKeyButton({ apiKey, onRevoked }: { apiKey: ApiKeyOut; onRevoked: () => void }) {
  const { canWrite } = useWriteAccess("admin");

  return (
    <ConfirmDialog
      trigger={
        <Button type="button" variant="ghost" size="sm" disabled={!canWrite} title={canWrite ? undefined : writeAccessReason("admin")}>
          Revoke
        </Button>
      }
      title={`Revoke "${apiKey.name}"?`}
      description="Immediately stops this key from authenticating any further MCP tool calls. The row stays in the list for the audit trail."
      confirmLabel="Revoke"
      onConfirm={async () => {
        try {
          await api.delete(`api-keys/${apiKey.id}`);
          toast.success(`Revoked "${apiKey.name}"`);
          onRevoked();
        } catch (err) {
          toast.error(`Couldn't revoke "${apiKey.name}" — ${errorMessage(err)}`);
        }
      }}
    />
  );
}
