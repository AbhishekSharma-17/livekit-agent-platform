"use client";

import * as React from "react";
import Link from "next/link";
import { ActivityIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState } from "@/components/shared/empty-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner } from "@/components/console/shared/error-banner";

import type { AuditOut } from "./api-types";
import { useRememberedChoice } from "@/components/shared/list-search";
import { RowsSkeleton } from "./settings-card";
import { isAgentActivityRow, useActiveWorkspace, useAgentActivityPage, useAgentKeys } from "./use-settings-queries";
import { EMPTY_VALUE } from "@/lib/format";

const PAGE_SIZE = 100;

/**
 * Route builders for `AuditOut.target_type` (`auth/audit.py::route_target`:
 * the first `/v1/…` path segment, e.g. `agents`, `knowledge-bases`,
 * `connections`; a few v2 routers also log a hand-written singular form). No
 * entry means "no console detail page for that kind" — the row still shows
 * the type and id as plain text rather than a dead link.
 */
const TARGET_ROUTE: Record<string, (id: string) => string> = {
  agent: (id) => `/console/agents/${id}`,
  agents: (id) => `/console/agents/${id}`,
  "knowledge-base": (id) => `/console/knowledge/${id}`,
  "knowledge-bases": (id) => `/console/knowledge/${id}`,
  connection: (id) => `/console/connections/${id}`,
  connections: (id) => `/console/connections/${id}`,
  session: (id) => `/console/sessions/${id}`,
  sessions: (id) => `/console/sessions/${id}`,
};

function targetHref(row: AuditOut): string | undefined {
  if (!row.target_id) return undefined;
  return TARGET_ROUTE[row.target_type]?.(row.target_id);
}

interface ClientPayload {
  product?: string;
  name?: string;
  tool?: string;
  call?: string;
}

function clientPayload(row: AuditOut): ClientPayload {
  const value = row.payload?.client;
  return value && typeof value === "object" ? (value as ClientPayload) : {};
}

/**
 * "Agent activity" (docs/v3/AGENT-ACCESS.md §5 item 3): `GET /v1/audit`
 * filtered client-side to rows an agent key wrote (D-V3-9: `actor_type ===
 * "api_key"` with `payload.client` set), newest first, with a key filter and
 * "Load more" paging over the raw audit log (the route has no
 * `actor_type`/`client` query param to filter server-side).
 */
export function AgentActivityTable() {
  const { workspace } = useActiveWorkspace();
  const keysQuery = useAgentKeys(workspace?.id);
  const agentKeys = React.useMemo(() => keysQuery.data?.items ?? [], [keysQuery.data]);

  const [offset, setOffset] = React.useState(0);
  const [rows, setRows] = React.useState<AuditOut[]>([]);
  // Remembered per person; a key id that no longer exists falls back to "all".
  const keyIds = React.useMemo(() => agentKeys.map((key) => key.id), [agentKeys]);
  const [rememberedKey, setKeyFilter] = useRememberedChoice("agent-activity-key", "all", keyIds);
  // The filter only applies while its control is shown (two or more keys).
  const keyFilter = agentKeys.length > 1 ? rememberedKey : "all";
  const page = useAgentActivityPage(workspace?.id, PAGE_SIZE, offset);

  React.useEffect(() => {
    if (!page.data) return;
    setRows((prev) => (offset === 0 ? page.data.items : [...prev, ...page.data.items]));
  }, [page.data, offset]);

  const agentRows = React.useMemo(() => rows.filter(isAgentActivityRow), [rows]);
  const filteredRows = React.useMemo(
    () => (keyFilter === "all" ? agentRows : agentRows.filter((row) => row.actor_id === keyFilter)),
    [agentRows, keyFilter],
  );
  const hasMore = page.data ? offset + PAGE_SIZE < page.data.total : false;

  const columns: ResponsiveTableColumn<AuditOut>[] = [
    {
      id: "time",
      header: "Time",
      cell: (row) => <RelativeTime iso={row.ts} withExact />,
    },
    {
      id: "key",
      header: "Key",
      cell: (row) => {
        const key = agentKeys.find((k) => k.id === row.actor_id);
        return <span className="truncate text-label text-foreground">{key?.name ?? row.actor_id ?? EMPTY_VALUE}</span>;
      },
    },
    {
      id: "client",
      header: "Client",
      cell: (row) => <span className="text-caption text-text-secondary">{clientPayload(row).name ?? EMPTY_VALUE}</span>,
    },
    {
      id: "tool",
      header: "Tool",
      cell: (row) => <span className="font-mono text-caption text-text-secondary">{clientPayload(row).tool ?? EMPTY_VALUE}</span>,
    },
    {
      id: "action",
      header: "Action",
      cell: (row) => (
        <span className="font-mono text-caption text-text-secondary" title={row.action}>
          {row.action}
        </span>
      ),
    },
    {
      id: "target",
      header: "Target",
      interactive: true,
      cell: (row) => {
        const href = targetHref(row);
        const label = row.target_id ? `${row.target_type} ${row.target_id.slice(0, 8)}` : row.target_type || EMPTY_VALUE;
        if (!href) return <span className="text-caption text-text-secondary">{label}</span>;
        return (
          <Link href={href} className="text-caption text-brand underline-offset-[3px] hover:underline">
            {label}
          </Link>
        );
      },
    },
  ];

  if ((page.isLoading && offset === 0) || !workspace) {
    return <RowsSkeleton label="Loading agent activity" />;
  }
  if (page.isError) {
    return <ErrorBanner error={page.error} context={{ action: "load agent activity" }} onRetry={() => void page.refetch()} />;
  }
  if (agentRows.length === 0) {
    return (
      <EmptyState
        variant="plain"
        icon={ActivityIcon}
        title="No agent changes yet"
        description="Changes an AI agent makes through its key show up here."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {agentKeys.length > 1 ? (
        <div className="flex items-center gap-2">
          <Select value={keyFilter} onValueChange={setKeyFilter}>
            <SelectTrigger aria-label="Filter by agent key" className="w-full max-w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All agent keys</SelectItem>
              {agentKeys.map((key) => (
                <SelectItem key={key.id} value={key.id}>
                  {key.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      {filteredRows.length === 0 ? (
        <EmptyState
          variant="plain"
          icon={ActivityIcon}
          title="No activity for this key yet"
          action={
            <Button type="button" size="sm" onClick={() => setKeyFilter("all")}>
              Show all agent keys
            </Button>
          }
        />
      ) : (
        <ResponsiveTable<AuditOut>
          columns={columns}
          rows={filteredRows}
          label="Agent activity"
          getRowKey={(row) => row.id}
          renderCard={(row) => {
            const key = agentKeys.find((k) => k.id === row.actor_id);
            return (
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-label font-medium text-foreground">{key?.name ?? row.actor_id ?? EMPTY_VALUE}</span>
                  <RelativeTime iso={row.ts} className="shrink-0 text-caption text-text-secondary" />
                </div>
                <div className="truncate font-mono text-caption text-text-secondary">{clientPayload(row).tool ?? row.action}</div>
              </div>
            );
          }}
        />
      )}

      {hasMore ? (
        <Button
          type="button"
          size="sm"
          className="self-start"
          onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
          busy={page.isFetching}
          busyLabel="Loading…"
        >
          Load more
        </Button>
      ) : null}
    </div>
  );
}
