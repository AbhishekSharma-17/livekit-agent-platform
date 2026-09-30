"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { PencilIcon, PlayIcon, PlusIcon, Trash2Icon, WrenchIcon } from "lucide-react";

import { EmptyState, NoMatches } from "@/components/shared/empty-state";
import { SkeletonRows } from "@/components/shared/loading-state";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { StatusPill } from "@/components/shared/status-chip";
import { Tag } from "@/components/shared/tag";
import { VendorMark } from "@/components/shared/vendor-mark";
import { Button } from "@/components/ui/button";
import { useAgents, useDeleteTool, useProviders, useToolProviderConnections, useTools } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { readOnlyCopy } from "@/components/console/shared/permission";
import { DatasetToolEditorDialog } from "@/components/console/tools/dataset-tool-editor-dialog";
import { DryRunDialog } from "@/components/console/tools/dry-run-dialog";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { Highlight, ListToolbar, SEARCH_THRESHOLD, matchesQuery, useRememberedChoice } from "@/components/shared/list-search";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { ProviderToolEditorDialog } from "@/components/console/tools/provider-tool-editor-dialog";
import { requestSummary } from "@/components/console/tools/tool-row";
import { ToolTemplateDialog } from "@/components/console/tools/tool-template-dialog";
import { useWriteGate } from "@/components/console/shared/write-gate";
import type { AppConnectionOut, ProviderSpec, ProviderToolDefinition, ToolOut } from "@/contracts/lkap-contracts";

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
  if (tool.kind === "dataset") return "Lookup table";
  return tool.kind === "http" ? "HTTP" : "MCP";
}

/** The connected app's name for a `provider` tool's chip (looked up by `connection_id`, docs/v5/_asks.md #16). */
function appNameFor(tool: ToolOut, connectionsById: Map<string, AppConnectionOut>): string | null {
  if (!isProviderTool(tool)) return null;
  const definition = tool.definition as { connection_id?: string; toolkit?: string };
  const connection = definition.connection_id ? connectionsById.get(definition.connection_id) : undefined;
  return connection?.toolkit_name ?? connection?.toolkit ?? definition.toolkit ?? "App";
}

type ToolKind = "http" | "mcp" | "dataset" | "app";
type KindFilter = "all" | ToolKind;
const KIND_FILTERS: readonly KindFilter[] = ["all", "http", "mcp", "dataset", "app"];
const KIND_FILTER_LABEL: Record<ToolKind, string> = { http: "HTTP", mcp: "MCP", dataset: "Lookup", app: "Apps" };

function kindOf(tool: ToolOut): ToolKind {
  if (isProviderTool(tool)) return "app";
  if (tool.kind === "dataset") return "dataset";
  return tool.kind === "http" ? "http" : "mcp";
}

/**
 * The Tools tab's header actions (docs/ui/DESIGN-SYSTEM.md section 7.3): the
 * secondary "add" routes first, then the one primary, "Add HTTP tool", last.
 * Below the builder floor they are replaced by a read-only note (decision
 * D12); while the role loads they render disabled.
 */
export function ToolsAddActions() {
  const toolsQuery = useTools();
  const providersQuery = useProviders();
  const secretBagSpec = providersQuery.data?.providers.find((p) => p.kind === "secret_bag");
  const refetch = () => void toolsQuery.refetch();
  const gate = useWriteGate();
  if (!gate.show) return <ReadOnlyNote>{readOnlyCopy("builder", "add tools")}</ReadOnlyNote>;
  return (
    <>
      <ToolTemplateDialog
        agentId={null}
        secretBagSpec={secretBagSpec}
        onInstantiated={refetch}
        trigger={
          <Button type="button" variant="secondary" disabled={gate.pending}>
            <PlusIcon aria-hidden="true" /> From a template
          </Button>
        }
      />
      <McpToolEditorDialog
        agentId={null}
        secretBagSpec={secretBagSpec}
        onSaved={refetch}
        trigger={
          <Button type="button" variant="secondary" disabled={gate.pending}>
            <PlusIcon aria-hidden="true" /> Add MCP server
          </Button>
        }
      />
      <DatasetToolEditorDialog
        agentId={null}
        onSaved={refetch}
        trigger={
          <Button type="button" variant="secondary" disabled={gate.pending}>
            <PlusIcon aria-hidden="true" /> Add lookup tool
          </Button>
        }
      />
      <HttpToolEditorDialog
        agentId={null}
        secretBagSpec={secretBagSpec}
        onSaved={refetch}
        trigger={
          <Button type="button" variant="primary" disabled={gate.pending}>
            <PlusIcon aria-hidden="true" /> Add HTTP tool
          </Button>
        }
      />
    </>
  );
}

/**
 * `/console/tools` (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1, §3 WP-5
 * "Change"): every HTTP tool and MCP server across agents, plus tools
 * created with no agent (`agent_id: null`, attachable from any agent's
 * Tools section). The page header and its add actions sit above the tab
 * strip (`ToolsPageTabs`, `ToolsAddActions`); this is the list itself: a
 * search field and a kind filter once there are six or more tools
 * (docs/ui/DESIGN-SYSTEM.md section 9; the filter is remembered per person),
 * a `ResponsiveTable` with row actions, and separate "nothing yet" and
 * "no matches" states.
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
  const gate = useWriteGate();
  const [query, setQuery] = React.useState("");
  const [kind, setKind] = useRememberedChoice<KindFilter>("tools-kind", "all", KIND_FILTERS, { legacyKeys: ["lkap.tools.kind"] });

  const refetch = () => void toolsQuery.refetch();

  if (toolsQuery.isLoading) {
    return <SkeletonRows label="Loading tools" rows={4} rowClassName="h-14" />;
  }

  if (toolsQuery.isError) {
    return <ErrorBanner error={toolsQuery.error} context={{ action: "load tools" }} onRetry={refetch} />;
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

  const counts: Record<ToolKind, number> = { http: 0, mcp: 0, dataset: 0, app: 0 };
  for (const tool of tools) counts[kindOf(tool)] += 1;
  const filtering = query.trim() !== "" || kind !== "all";
  const showToolbar = tools.length >= SEARCH_THRESHOLD || filtering;
  const visible = tools.filter(
    (tool) =>
      (kind === "all" || kindOf(tool) === kind) &&
      matchesQuery([tool.name, requestSummary(tool), kindLabel(tool), scopeOf(tool).label, appNameFor(tool, connectionsById)], query),
  );
  const clearFilters = () => {
    setQuery("");
    setKind("all");
  };
  const kindOptions = [
    { value: "all" as KindFilter, label: "All", count: tools.length },
    ...(Object.keys(KIND_FILTER_LABEL) as ToolKind[])
      .filter((key) => counts[key] > 0 || key === kind)
      .map((key) => ({ value: key as KindFilter, label: KIND_FILTER_LABEL[key], count: counts[key] })),
  ];

  const nameBlock = (tool: ToolOut) => (
    <div className="min-w-0">
      <div className="truncate font-mono text-body text-foreground">
        <Highlight text={tool.name} query={query} />
      </div>
      <div className="truncate text-caption text-text-secondary">
        <Highlight text={requestSummary(tool)} query={query} />
      </div>
    </div>
  );

  const kindCell = (tool: ToolOut) =>
    isProviderTool(tool) ? (
      <Tag>
        <VendorMark vendor={appNameFor(tool, connectionsById) ?? "App"} size="sm" />
        App
      </Tag>
    ) : (
      <span className="text-text-secondary">{kindLabel(tool)}</span>
    );

  const statusPill = (tool: ToolOut) =>
    tool.enabled ? <StatusPill tone="success">Enabled</StatusPill> : <StatusPill tone="neutral">Disabled</StatusPill>;

  const columns: ResponsiveTableColumn<ToolOut>[] = [
    { id: "name", header: "Name", cell: nameBlock },
    { id: "kind", header: "Kind", cell: kindCell },
    {
      id: "scope",
      header: "Used by",
      cell: (tool) => {
        const scope = scopeOf(tool);
        return scope.href ? (
          <Link href={scope.href} className="text-brand underline-offset-3 hover:underline">
            {scope.label}
          </Link>
        ) : (
          <span className="text-text-secondary">{scope.label}</span>
        );
      },
    },
    { id: "status", header: "Status", cell: statusPill },
    {
      id: "updated",
      header: "Updated",
      cell: (tool) => <RelativeTime iso={tool.updated_at} className="text-text-secondary" />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (tool) => <ToolActions tool={tool} secretBagSpec={secretBagSpec} onRefetch={refetch} />,
    },
  ];

  return (
    <div>
      {showToolbar ? (
        <ListToolbar
          items="tools"
          query={query}
          onQueryChange={setQuery}
          filter={{ label: "Filter by kind", value: kind, onValueChange: setKind, options: kindOptions }}
        />
      ) : null}
      <ResponsiveTable
        columns={columns}
        rows={visible}
        label="Tools"
        getRowKey={(tool) => tool.id}
        renderCard={(tool) => (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-start justify-between gap-2">
              {nameBlock(tool)}
              <ToolActions tool={tool} secretBagSpec={secretBagSpec} onRefetch={refetch} />
            </div>
            <div className="flex flex-wrap items-center gap-2 text-caption text-text-secondary">
              {kindCell(tool)}
              <span aria-hidden="true">·</span>
              <span>{scopeOf(tool).label}</span>
              {statusPill(tool)}
            </div>
          </div>
        )}
        empty={
          filtering ? (
            <NoMatches items="tools" query={query} onClear={clearFilters} />
          ) : (
            <EmptyState
              icon={WrenchIcon}
              title="No tools yet"
              description={
                gate.show
                  ? "Connect an API or MCP server the agent can call, then attach it from an agent's Tools section."
                  : readOnlyCopy("builder", "add tools")
              }
              action={
                gate.show ? (
                  <HttpToolEditorDialog
                    agentId={null}
                    secretBagSpec={secretBagSpec}
                    onSaved={refetch}
                    trigger={
                      <Button type="button" variant="secondary" disabled={gate.pending}>
                        <PlusIcon aria-hidden="true" /> Add HTTP tool
                      </Button>
                    }
                  />
                ) : undefined
              }
            />
          )
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
 * that only makes sense inside an agent's Tools section. Edit and Delete are
 * not rendered for viewers (decision D12); Dry run stays, it changes nothing.
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
  const gate = useWriteGate();

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
    return <p className="text-right text-caption text-text-secondary">Managed from the Connected apps card</p>;
  }

  const editTrigger = (
    <Button type="button" variant="ghost" size="icon-sm" aria-label={`Edit ${tool.name}`} disabled={gate.pending}>
      <PencilIcon aria-hidden="true" />
    </Button>
  );

  return (
    <div className="flex items-center justify-end gap-1">
      {tool.kind === "http" ? (
        <DryRunDialog
          toolId={tool.id}
          trigger={
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Dry run ${tool.name}`}>
              <PlayIcon aria-hidden="true" />
            </Button>
          }
        />
      ) : null}
      {!gate.show ? null : tool.kind === "http" ? (
        <HttpToolEditorDialog
          agentId={tool.agent_id ?? null}
          tool={tool}
          secretBagSpec={secretBagSpec}
          onSaved={onRefetch}
          trigger={editTrigger}
        />
      ) : tool.kind === "provider" ? (
        // R-V5-8, V5-50: a `ProviderToolDefinition` gets its own dialog, never the MCP one.
        <ProviderToolEditorDialog
          tool={tool as ToolOut & { definition: ProviderToolDefinition }}
          onSaved={onRefetch}
          trigger={editTrigger}
        />
      ) : tool.kind === "dataset" ? (
        // V6-19: a lookup-table tool (`DatasetToolDefinition`, D-V6-27) — its own editor, never the MCP one.
        <DatasetToolEditorDialog agentId={tool.agent_id ?? null} tool={tool} onSaved={onRefetch} trigger={editTrigger} />
      ) : (
        <McpToolEditorDialog
          agentId={tool.agent_id ?? null}
          tool={tool}
          secretBagSpec={secretBagSpec}
          onSaved={onRefetch}
          trigger={editTrigger}
        />
      )}
      {gate.show ? (
        <ConfirmDialog
          trigger={
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Delete ${tool.name}`} disabled={gate.pending}>
              <Trash2Icon aria-hidden="true" />
            </Button>
          }
          title={`Delete “${tool.name}”?`}
          description="This removes the tool everywhere it's attached."
          confirmLabel="Delete tool"
          onConfirm={handleDelete}
        />
      ) : null}
    </div>
  );
}
