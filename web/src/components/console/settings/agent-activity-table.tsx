"use client";

import * as React from "react";
import Link from "next/link";
import { ActivityIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState } from "@/components/shared/empty-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { SkeletonRows } from "@/components/shared/loading-state";

import type { AuditOut } from "./api-types";
import { isAgentActivityRow, useActiveWorkspace, useAgentActivityPage, useAgentKeys } from "./use-settings-queries";

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
  const agentKeys = keysQuery.data?.items ?? [];

  const [offset, setOffset] = React.useState(0);
  const [rows, setRows] = React.useState<AuditOut[]>([]);
  const [keyFilter, setKeyFilter] = React.useState<string>("all");
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
        return <span className="truncate text-sm text-foreground">{key?.name ?? row.actor_id ?? "—"}</span>;
      },
    },
    {
      id: "client",
      header: "Client",
      cell: (row) => <span className="text-xs text-muted-foreground">{clientPayload(row).name ?? "—"}</span>,
    },
    {
      id: "tool",
      header: "Tool",
      cell: (row) => <span className="font-mono text-xs text-muted-foreground">{clientPayload(row).tool ?? "—"}</span>,
    },
    {
      id: "action",
      header: "Action",
      cell: (row) => (
        <span className="font-mono text-xs text-muted-foreground" title={row.action}>
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
        const label = row.target_id ? `${row.target_type} ${row.target_id.slice(0, 8)}` : row.target_type || "—";
        if (!href) return <span className="text-xs text-muted-foreground">{label}</span>;
        return (
          <Link href={href} className="text-xs text-brand-text underline underline-offset-2 hover:text-foreground">
            {label}
          </Link>
        );
      },
    },
  ];

  if (page.isLoading && offset === 0) {
    return <SkeletonRows label="Loading agent activity" rowClassName="h-12" />;
  }
  if (page.isError) {
    return <ErrorBanner message={`Couldn't load agent activity — ${errorMessage(page.error)}`} onRetry={() => page.refetch()} />;
  }
  if (agentRows.length === 0) {
    return <EmptyState icon={ActivityIcon} title="No agent changes yet" compact />;
  }

  return (
    <div className="flex flex-col gap-3">
      {agentKeys.length > 1 ? (
        <div className="flex items-center gap-2">
          <Select value={keyFilter} onValueChange={setKeyFilter}>
            <SelectTrigger aria-label="Filter by agent key" className="w-56">
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
        <EmptyState icon={ActivityIcon} title="No activity for this key yet" compact />
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
                  <span className="truncate text-sm font-medium text-foreground">{key?.name ?? row.actor_id ?? "—"}</span>
                  <RelativeTime iso={row.ts} className="shrink-0 text-xs text-muted-foreground" />
                </div>
                <div className="truncate font-mono text-xs text-muted-foreground">{clientPayload(row).tool ?? row.action}</div>
              </div>
            );
          }}
        />
      )}

      {hasMore ? (
        <Button type="button" variant="outline" size="sm" onClick={() => setOffset((prev) => prev + PAGE_SIZE)} disabled={page.isFetching}>
          {page.isFetching ? "Loading…" : "Load more"}
        </Button>
      ) : null}
    </div>
  );
}
