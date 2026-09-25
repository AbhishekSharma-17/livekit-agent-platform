"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { PencilIcon, PlayIcon, PlusIcon, Trash2Icon, WrenchIcon } from "lucide-react";

import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { Button } from "@/components/ui/button";
import { useAgents, useDeleteTool, useProviders, useToolProviderConnections, useTools } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { DryRunDialog } from "@/components/console/tools/dry-run-dialog";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { ProviderToolEditorDialog } from "@/components/console/tools/provider-tool-editor-dialog";
import { requestSummary } from "@/components/console/tools/tool-row";
import type { AppConnectionOut, ProviderSpec, ProviderToolDefinition, ToolOut } from "@/contracts/lkap-contracts";
import { PageHeader } from "@/components/shared/page-header";
import { SkeletonRows } from "@/components/shared/loading-state";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";

/**
 * V5-47's `ProviderToolDefinition`/`McpServerOrigin` — checked structurally
 * rather than via `tool.kind`/`definition.kind` (`docs/v5/_asks.md` #20: the
 * generated union makes `kind` optional on the definition, so V5-47's own
 * code checks for the field that only that variant carries).
 */
function isProviderTool(tool: ToolOut): boolean {
  return "tool_slug" in tool.definition;
}

/**
 * A Composio-provisioned MCP server or Tool Router session (docs/v5/COMPOSIO.md
 * §3, §6) — managed from the agent's Connected apps card, not edited here.
 * Exported so `tools-tab.tsx` (agent-scoped) can filter these out of its own
 * "MCP servers" section too: `provisioning.py` creates them with
 * `agent_id=agent.id`, so `useTools(agentId)` returns them there, and
 * without the filter they'd show with the usual Edit/Delete/attach
 * controls, contradicting the read-only rule this file enforces below.
 */
export function originOf(tool: ToolOut): "server" | "router" | null {
  if (!("origin" in tool.definition) || !tool.definition.origin) return null;
  return tool.definition.origin.kind;
}

/** "App" for a connected-app action, "App server" / "Tool finder" for a Composio-origin MCP entry, else the plain kind. */
function kindLabel(tool: ToolOut): string {
  if (isProviderTool(tool)) return "App";
  const origin = originOf(tool);
  if (origin === "server") return "App server";
  if (origin === "router") return "Tool finder";
  return tool.kind === "http" ? "HTTP" : "MCP";
}

/** The connected app's name for a `provider` tool's chip (looked up by `connection_id`, docs/v5/_asks.md #16). */
function appNameFor(tool: ToolOut, connectionsById: Map<string, AppConnectionOut>): string | null {
  if (!isProviderTool(tool)) return null;
  const definition = tool.definition as { connection_id?: string; toolkit?: string };
  const connection = definition.connection_id ? connectionsById.get(definition.connection_id) : undefined;
  return connection?.toolkit_name ?? connection?.toolkit ?? definition.toolkit ?? "App";
}

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
  // Only fetched to label a `provider` tool's App chip with its connected
  // app's name (docs/v5/_asks.md #16) — a 404/disabled-Apps response just
  // means every such chip falls back to the tool's own `toolkit` field.
  const connectionsQuery = useToolProviderConnections();
  const connectionsById = new Map((connectionsQuery.data?.items ?? []).map((c) => [c.id, c]));
  const secretBagSpec = providersQuery.data?.providers.find((p) => p.kind === "secret_bag");
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  // The two "add" actions live in the page header, like every other console
  // list's primary action (UI_UX_SPEC §3.2), and stay reachable while the
  // list loads or errors.
  const refetch = () => void toolsQuery.refetch();
  const header = (
    <PageHeader
      title="Tools"
      description="HTTP tools and MCP servers, shared across agents."
      actions={
        <>
          <McpToolEditorDialog
            agentId={null}
            secretBagSpec={secretBagSpec}
            onSaved={refetch}
            trigger={
              <Button
                type="button"
                variant="outline"
                disabled={!canWrite}
                title={canWrite ? undefined : writeReason}
              >
                <PlusIcon className="size-3.5" /> Add MCP server
              </Button>
            }
          />
          <HttpToolEditorDialog
            agentId={null}
            secretBagSpec={secretBagSpec}
            onSaved={refetch}
            trigger={
              <Button type="button" disabled={!canWrite} title={canWrite ? undefined : writeReason}>
                <PlusIcon className="size-3.5" /> Add HTTP tool
              </Button>
            }
          />
        </>
      }
    />
  );

  if (toolsQuery.isLoading) {
    return (
      <div>
        {header}
        <SkeletonRows label="Loading tools" rowClassName="h-12" />
      </div>
    );
  }

  if (toolsQuery.isError) {
    return (
      <div>
        {header}
        <ErrorBanner message={`Couldn't load tools — ${errorMessage(toolsQuery.error)}`} onRetry={refetch} />
      </div>
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
      cell: (tool) =>
        isProviderTool(tool) ? (
          <StatusChip tone="info" size="sm">
            <VendorMark vendor={appNameFor(tool, connectionsById) ?? "App"} size="sm" />
            App
          </StatusChip>
        ) : (
          <span className="text-muted-foreground">{kindLabel(tool)}</span>
        ),
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
    <div>
      {header}
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
                <span>{kindLabel(tool)}</span>
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
                onSaved={refetch}
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
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  async function handleDelete() {
    try {
      await deleteTool.mutateAsync(tool.id);
      toast.success(`${tool.name} deleted.`);
      onRefetch();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  // A Composio-provisioned MCP server or Tool Router session (docs/v5/COMPOSIO.md
  // §6): the agent's own Connected apps card creates, updates and removes
  // this row as the mode changes, so no edit or delete control is offered here.
  if (originOf(tool)) {
    return <p className="text-right text-xs text-muted-foreground">Managed from the Connected apps card</p>;
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
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Edit ${tool.name}`}
              disabled={!canWrite}
              title={canWrite ? undefined : writeReason}
            >
              <PencilIcon className="size-3.5" />
            </Button>
          }
        />
      ) : tool.kind === "provider" ? (
        // R-V5-8, V5-50: a `ProviderToolDefinition` gets its own dialog, never the MCP one.
        <ProviderToolEditorDialog
          tool={tool as ToolOut & { definition: ProviderToolDefinition }}
          onSaved={onRefetch}
          trigger={
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Edit ${tool.name}`}
              disabled={!canWrite}
              title={canWrite ? undefined : writeReason}
            >
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
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Edit ${tool.name}`}
              disabled={!canWrite}
              title={canWrite ? undefined : writeReason}
            >
              <PencilIcon className="size-3.5" />
            </Button>
          }
        />
      )}
      <ConfirmDialog
        trigger={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete ${tool.name}`}
            disabled={!canWrite}
            title={canWrite ? undefined : writeReason}
          >
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
