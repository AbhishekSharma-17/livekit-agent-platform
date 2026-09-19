"use client";

import * as React from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSessions } from "@/components/console/lib/api-hooks";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { SessionOut } from "@/contracts/lkap-contracts";

const STATUS_VARIANT: Record<SessionOut["status"], "default" | "secondary" | "destructive"> = {
  created: "secondary",
  active: "default",
  ended: "secondary",
  failed: "destructive",
};

const ALL_STATUSES = "__all__";

/**
 * `failed` rows the stale-session sweep produced (DECISIONS-W2 D-W2-2b) get a
 * muted chip instead of the usual destructive "failed" badge — they never ran
 * a real call, so they shouldn't read as an error the way a mid-call crash does.
 */
const SWEPT_ERRORS = new Set(["never started", "summary never received"]);

function sweptChip(session: SessionOut): string | null {
  return session.status === "failed" && session.error && SWEPT_ERRORS.has(session.error)
    ? session.error
    : null;
}

export function SessionsTable() {
  const [agentId, setAgentId] = React.useState("");
  const [status, setStatus] = React.useState("");
  const { data, isLoading, isError, error, refetch } = useSessions(agentId || undefined, status || undefined);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Input
          value={agentId}
          onChange={(event) => setAgentId(event.target.value)}
          placeholder="Filter by agent id"
          className="w-56"
        />
        <Select value={status || ALL_STATUSES} onValueChange={(next) => setStatus(next === ALL_STATUSES ? "" : next)}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_STATUSES}>All statuses</SelectItem>
            <SelectItem value="created">Created</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="ended">Ended</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : isError ? (
        <ErrorBanner message={`Could not reach the api: ${errorMessage(error)}`} onRetry={() => refetch()} />
      ) : (data?.items.length ?? 0) === 0 ? (
        <EmptyState title="No sessions yet" description="Sessions appear here once someone opens a test call or the public session page." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Agent</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Mode</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Ended</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.items.map((session) => (
              <TableRow key={session.id}>
                <TableCell className="font-medium">
                  <Link href={`/console/sessions/${session.id}`} className="hover:underline">
                    {session.agent_name}
                  </Link>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant={STATUS_VARIANT[session.status]}>{session.status}</Badge>
                    {sweptChip(session) ? (
                      <Badge variant="outline" className="text-muted-foreground">
                        {sweptChip(session)}
                      </Badge>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">{session.pipeline_mode}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{new Date(session.created_at).toLocaleString()}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {session.ended_at ? new Date(session.ended_at).toLocaleString() : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
