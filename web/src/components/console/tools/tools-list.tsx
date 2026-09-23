"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { PencilIcon, PlayIcon, PlusIcon, Trash2Icon, WrenchIcon } from "lucide-react";

import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents, useDeleteTool, useProviders, useTools } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { DryRunDialog } from "@/components/console/tools/dry-run-dialog";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { requestSummary } from "@/components/console/tools/tool-row";
import type { ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";

/**
 * `/console/tools` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1, §3 WP-5
 * "Change"): every HTTP tool and MCP server across agents, plus tools
 * created with no agent (`agent_id: null`, attachable from any agent's
 * Tools section). WP-1's sidebar links here; there is no v1 UX for this
 * page (it did not exist before v2), so this mirrors the Knowledge list's
 * conventions (`kb-list.tsx`): a `ResponsiveTable`, row actions, an empty
 * state.
 */
export function ToolsList() {
  const toolsQuery = useTools();
  const agentsQuery = useAgents();
  const providersQuery = useProviders();
  const secretBagSpec = providersQuery.data?.providers.find((p) => p.kind === "secret_bag");

  if (toolsQuery.isLoading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (toolsQuery.isError) {
    return (
      <ErrorBanner message={`Could not reach the api: ${errorMessage(toolsQuery.error)}`} onRetry={() => toolsQuery.refetch()} />
    );
  }

  const tools = toolsQuery.data?.items ?? [];
  const agentsById = new Map((agentsQuery.data?.items ?? []).map((agent) => [agent.id, agent]));

  function scopeOf(tool: ToolOut): { label: string; href?: string } {
    if (!tool.agent_id) return { label: "Shared" };
    const agent = agentsById.get(tool.agent_id);
    return agent
      ? { label: agent.name, href: `/console/agents/${agent.id}?section=tools` }
      : { label: "One agent" };
  }

  const columns: ResponsiveTableColumn<ToolOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (tool) => (
        <div className="min-w-0">
          <div className="truncate font-mono text-sm text-foreground">{tool.name}</div>
          <div className="truncate text-xs text-muted-foreground">{requestSummary(tool)}</div>
        </div>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      cell: (tool) => <span className="text-muted-foreground">{tool.kind === "http" ? "HTTP" : "MCP"}</span>,
    },
    {
      id: "scope",
      header: "Used by",
      cell: (tool) => {
        const scope = scopeOf(tool);
        return scope.href ? (
          <Link href={scope.href} className="text-brand-text hover:underline">
            {scope.label}
          </Link>
        ) : (
          <span className="text-muted-foreground">{scope.label}</span>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      cell: (tool) => (tool.enabled ? <StatusChip tone="success">Enabled</StatusChip> : <StatusChip tone="neutral">Disabled</StatusChip>),
    },
    {
      id: "updated",
      header: "Updated",
      cell: (tool) => <RelativeTime iso={tool.updated_at} />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (tool) => <ToolActions tool={tool} secretBagSpec={secretBagSpec} onRefetch={() => void toolsQuery.refetch()} />,
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <HttpToolEditorDialog
          agentId={null}
          secretBagSpec={secretBagSpec}
          onSaved={() => void toolsQuery.refetch()}
          trigger={
            <Button type="button" variant="outline" size="sm">
              <PlusIcon className="size-3.5" /> Add HTTP tool
            </Button>
          }
        />
        <McpToolEditorDialog
          agentId={null}
          secretBagSpec={secretBagSpec}
          onSaved={() => void toolsQuery.refetch()}
          trigger={
            <Button type="button" variant="outline" size="sm">
              <PlusIcon className="size-3.5" /> Add MCP server
            </Button>
          }
        />
      </div>

      <ResponsiveTable
        columns={columns}
        rows={tools}
        label="Tools"
        getRowKey={(tool) => tool.id}
        renderCard={(tool) => {
          const scope = scopeOf(tool);
          return (
            <div className="flex flex-col gap-1">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-mono text-sm text-foreground">{tool.name}</div>
                  <div className="truncate text-xs text-muted-foreground">{requestSummary(tool)}</div>
                </div>
                <ToolActions tool={tool} secretBagSpec={secretBagSpec} onRefetch={() => void toolsQuery.refetch()} />
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>{tool.kind === "http" ? "HTTP" : "MCP"}</span>
                <span aria-hidden="true">·</span>
                <span>{scope.label}</span>
                <span aria-hidden="true">·</span>
                {tool.enabled ? <StatusChip tone="success">Enabled</StatusChip> : <StatusChip tone="neutral">Disabled</StatusChip>}
              </div>
            </div>
          );
        }}
        empty={
          <EmptyState
            icon={WrenchIcon}
            title="No tools yet"
            description="Connect an API or MCP server the agent can call, then attach it from an agent's Tools section."
            action={
              <HttpToolEditorDialog
                agentId={null}
                secretBagSpec={secretBagSpec}
                onSaved={() => void toolsQuery.refetch()}
                trigger={<Button type="button">Add HTTP tool</Button>}
              />
            }
          />
        }
      />
    </div>
  );
}

/**
 * Row actions as inline icon buttons — each editor/dialog owns its own
 * uncontrolled open state via its `trigger`, so (unlike a menu) nothing here
 * needs to reach into them to open one programmatically. Mirrors
 * `tool-row.tsx`'s buttons, minus the per-agent "Use in this agent" switch
 * that only makes sense inside an agent's Tools section.
 */
function ToolActions({
  tool,
  secretBagSpec,
  onRefetch,
}: {
  tool: ToolOut;
  secretBagSpec: ProviderSpec | undefined;
  onRefetch: () => void;
}) {
  const deleteTool = useDeleteTool();

  async function handleDelete() {
    try {
      await deleteTool.mutateAsync(tool.id);
      toast.success(`${tool.name} deleted.`);
      onRefetch();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex items-center justify-end gap-1">
      {tool.kind === "http" ? (
        <DryRunDialog
          toolId={tool.id}
          trigger={
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Dry run ${tool.name}`}>
              <PlayIcon className="size-3.5" />
            </Button>
          }
        />
      ) : null}
      {tool.kind === "http" ? (
        <HttpToolEditorDialog
          agentId={tool.agent_id ?? null}
          tool={tool}
          secretBagSpec={secretBagSpec}
          onSaved={onRefetch}
          trigger={
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Edit ${tool.name}`}>
              <PencilIcon className="size-3.5" />
            </Button>
          }
        />
      ) : (
        <McpToolEditorDialog
          agentId={tool.agent_id ?? null}
          tool={tool}
          secretBagSpec={secretBagSpec}
          onSaved={onRefetch}
          trigger={
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Edit ${tool.name}`}>
              <PencilIcon className="size-3.5" />
            </Button>
          }
        />
      )}
      <ConfirmDialog
        trigger={
          <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${tool.name}`}>
            <Trash2Icon className="size-3.5" />
          </Button>
        }
        title={`Delete "${tool.name}"?`}
        description="This removes the tool everywhere it's attached."
        onConfirm={handleDelete}
      />
    </div>
  );
}
