"use client";

import * as React from "react";
import { BotIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/shared/empty-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Tag, TagList } from "@/components/shared/tag";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { IfCan } from "@/components/console/shared/permission";

import { KeyStatus, RevokeKeyButton } from "./api-keys-tab";
import type { ApiKeyOut } from "./api-types";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { RowsSkeleton } from "./settings-card";
import { AGENT_KEY_CLIENTS, CALLS_WRITE_SCOPE } from "./snippets";
import { useAgentKeys, useActiveWorkspace, useInvalidateSettings } from "./use-settings-queries";
import { EMPTY_VALUE } from "@/lib/format";

const CLIENT_LABEL: Record<string, string> = Object.fromEntries(AGENT_KEY_CLIENTS.map((c) => [c.id, c.label]));

function clientLabel(client: string | null): string {
  if (!client) return EMPTY_VALUE;
  return CLIENT_LABEL[client] ?? client;
}

/** A scope as a tag; `calls:write` (outbound phone calls) stands out in the warning tone. */
function ScopeTag({ scope }: { scope: string }) {
  if (scope === CALLS_WRITE_SCOPE) {
    return (
      <Badge tone="warning" className="font-mono">
        {scope}
      </Badge>
    );
  }
  return <Tag className="font-mono">{scope}</Tag>;
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
  const keys = React.useMemo(() => keysQuery.data?.items ?? [], [keysQuery.data]);
  const search = useListSearch("agent-keys", keys, (key) => [key.name, key.prefix, clientLabel(key.client)]);
  const query = search.query;

  const revoke = (key: ApiKeyOut) =>
    key.revoked_at ? null : (
      <IfCan min="admin">
        <RevokeKeyButton
          apiKey={key}
          onRevoked={() => invalidate(workspace?.id)}
          description="Immediately stops this key from authenticating any further MCP tool calls. The row stays in the list for the audit trail."
          confirmLabel="Revoke"
        />
      </IfCan>
    );

  const columns: ResponsiveTableColumn<ApiKeyOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (key) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">
            <Highlight text={key.name} query={query} />
          </div>
          <div className="truncate font-mono text-caption text-text-secondary">{key.prefix}…</div>
        </div>
      ),
    },
    { id: "client", header: "Client", cell: (key) => <Tag>{clientLabel(key.client)}</Tag> },
    {
      id: "scopes",
      header: "Scopes",
      className: "min-w-48 whitespace-normal",
      cell: (key) => (
        <TagList>
          {key.scopes.map((scope) => (
            <ScopeTag key={scope} scope={scope} />
          ))}
        </TagList>
      ),
    },
    { id: "created", header: "Created", cell: (key) => <RelativeTime iso={key.created_at} /> },
    {
      id: "last_used",
      header: "Last used",
      cell: (key) =>
        key.last_used_at ? <RelativeTime iso={key.last_used_at} /> : <span className="text-text-secondary">Never</span>,
    },
    {
      id: "last_client",
      header: "Last client",
      cell: (key) => <span className="text-caption text-text-secondary">{clientLabel(key.last_client)}</span>,
    },
    {
      id: "expires",
      header: "Expires",
      cell: (key) =>
        key.expires_at ? <RelativeTime iso={key.expires_at} /> : <span className="text-text-secondary">Never</span>,
    },
    { id: "status", header: "Status", cell: (key) => <KeyStatus apiKey={key} /> },
    { id: "actions", header: <span className="sr-only">Actions</span>, align: "end", interactive: true, cell: revoke },
  ];

  if (keysQuery.isLoading || !workspace) {
    return <RowsSkeleton label="Loading agent keys" />;
  }
  if (keysQuery.isError) {
    return (
      <ErrorBanner error={keysQuery.error} context={{ action: "load agent keys" }} onRetry={() => void keysQuery.refetch()} />
    );
  }
  if (keys.length === 0) {
    return (
      <EmptyState
        variant="plain"
        icon={BotIcon}
        title="No agent keys yet"
        description="Connect an AI agent above to create the first one."
      />
    );
  }

  return (
    <>
      <ListSearchField search={search} label="Search agent keys" total={keys.length} />
      {search.noMatches ? (
        <ListNoMatches search={search} items="agent keys" />
      ) : (
        <ResponsiveTable<ApiKeyOut>
          columns={columns}
          rows={search.filtered}
          label="Agent keys"
          getRowKey={(key) => key.id}
          renderCard={(key) => (
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">
                  <Highlight text={key.name} query={query} />
                </div>
                <div className="text-caption text-text-secondary">
                  {clientLabel(key.client)} · <span className="font-mono">{key.prefix}…</span>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <KeyStatus apiKey={key} />
                {revoke(key)}
              </div>
            </div>
          )}
        />
      )}
    </>
  );
}
