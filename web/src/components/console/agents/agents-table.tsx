"use client";

import * as React from "react";
import Link from "next/link";
import { ExternalLinkIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAgents, useDeleteAgent, useUpdateAgent } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/console/shared/empty-state";
import { errorMessage, ErrorBanner } from "@/components/console/shared/error-banner";
import { CreateAgentDialog } from "@/components/console/agents/create-agent-dialog";
import type { AgentOut } from "@/contracts/lkap-contracts";

export function AgentsTable() {
  const { data, isLoading, isError, error, refetch } = useAgents();

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return <ErrorBanner message={`Could not reach the api: ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const agents = data?.items ?? [];

  if (agents.length === 0) {
    return (
      <EmptyState
        title="No agents yet"
        description="Create one from a pack to get a working pipeline, instructions and tools out of the box."
        action={<CreateAgentDialog />}
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Pack</TableHead>
          <TableHead>Mode</TableHead>
          <TableHead>Published</TableHead>
          <TableHead>Updated</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {agents.map((agent) => (
          <AgentRow key={agent.id} agent={agent} />
        ))}
      </TableBody>
    </Table>
  );
}

function AgentRow({ agent }: { agent: AgentOut }) {
  const updateAgent = useUpdateAgent(agent.id);
  const deleteAgent = useDeleteAgent();

  async function togglePublished(next: boolean) {
    try {
      await updateAgent.mutateAsync({ published: next });
      toast.success(next ? `${agent.name} published.` : `${agent.name} unpublished.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <TableRow>
      <TableCell className="font-medium">
        <Link href={`/console/agents/${agent.id}`} className="hover:underline">
          {agent.name}
        </Link>
        <div className="text-xs text-muted-foreground">/{agent.slug}</div>
      </TableCell>
      <TableCell>
        <Badge variant="secondary">{agent.pack_id}</Badge>
      </TableCell>
      <TableCell className="text-sm text-muted-foreground">{agent.config.pipeline.mode}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Switch checked={agent.published} onCheckedChange={togglePublished} disabled={updateAgent.isPending} />
          <span className="text-xs text-muted-foreground">{agent.published ? "Live" : "Draft"}</span>
        </div>
      </TableCell>
      <TableCell className="text-sm text-muted-foreground">{new Date(agent.updated_at).toLocaleString()}</TableCell>
      <TableCell>
        <div className="flex items-center justify-end gap-1">
          <Button asChild variant="ghost" size="sm">
            <Link href={`/s/${agent.slug}?mode=test`} target="_blank" rel="noopener noreferrer">
              Test call <ExternalLinkIcon className="size-3.5" />
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href={`/console/agents/${agent.id}`}>Edit</Link>
          </Button>
          <ConfirmDialog
            trigger={
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${agent.name}`}>
                <Trash2Icon className="size-3.5" />
              </Button>
            }
            title={`Delete "${agent.name}"?`}
            description="Agents that have sessions cannot be deleted (sessions are kept for the audit trail); unpublish it instead."
            confirmLabel="Delete"
            onConfirm={async () => {
              try {
                await deleteAgent.mutateAsync(agent.id);
                toast.success(`${agent.name} deleted.`);
              } catch (error) {
                toast.error(errorMessage(error));
              }
            }}
          />
        </div>
      </TableCell>
    </TableRow>
  );
}
