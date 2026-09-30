"use client";

import * as React from "react";
import { toast } from "sonner";
import { PencilIcon, PlayIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { StatusPill } from "@/components/shared/status-chip";
import { Tag } from "@/components/shared/tag";
import { useDeleteTool } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { DatasetToolEditorDialog } from "@/components/console/tools/dataset-tool-editor-dialog";
import { DryRunDialog } from "@/components/console/tools/dry-run-dialog";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { mcpAuthChip } from "@/components/console/tools/mcp-oauth-status";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { ProviderToolEditorDialog } from "@/components/console/tools/provider-tool-editor-dialog";
import { useWriteGate } from "@/components/console/shared/write-gate";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { McpHeaderAuth, McpNoAuth, McpOAuthAuth, ProviderSpec, ProviderToolDefinition, ToolOut } from "@/contracts/lkap-contracts";

/** "No auth" / "Header" / "Sign in" chip (V5-21 acceptance: "Tool rows show the auth mode chip"). */
function McpAuthTag({ auth }: { auth: McpNoAuth | McpHeaderAuth | McpOAuthAuth | undefined }) {
  const { label, tone } = mcpAuthChip(auth ?? {});
  return (
    <StatusPill tone={tone} size="sm">
      {label}
    </StatusPill>
  );
}

/** `method + host` per docs/UI_UX_SPEC.md §7.6 item 4 ("method + host"), not the full URL template. */
export function requestSummary(tool: ToolOut): string {
  if ("tool_slug" in tool.definition) {
    // V5-47: a connected app's action has no URL of its own. Its description
    // already carries the account's label as a "(<label>) " prefix whenever
    // the app has more than one account (R-V5-13 item 3, `materialise.py`'s
    // `AccountNaming.describe`, e.g. "(Work) Send an email") — showing it
    // here is how a builder tells which inbox a tool touches, with no extra
    // connections lookup needed on this side.
    return tool.definition.description || `App action · ${tool.definition.toolkit || "app"}`;
  }
  if ("dataset_id" in tool.definition) {
    // V6-16: a lookup-table tool has no URL; name the columns it looks rows up by.
    return `Lookup table · by ${tool.definition.key_columns.join(", ")}`;
  }
  if (tool.definition.kind === "http") {
    let host = tool.definition.url;
    try {
      host = new URL(tool.definition.url).host || tool.definition.url;
    } catch {
      // template URLs with a placeholder host aren't parseable; show the raw template
    }
    return `${tool.definition.method ?? "POST"} ${host}`;
  }
  try {
    return new URL(tool.definition.url).host;
  } catch {
    return tool.definition.url;
  }
}

/**
 * One tool inside an agent's Tools section: name, state, auth mode, the
 * "Use in this agent" switch and the row actions. Edit and Delete are not
 * rendered for viewers (decision D12); while the role loads they render
 * disabled so nothing appears and then disappears.
 */
export function ToolRow({
  tool,
  agentId,
  attached,
  onToggleAttach,
  onSaved,
  onDeleted,
  secretBagSpec,
}: {
  tool: ToolOut;
  agentId: string;
  attached: boolean;
  onToggleAttach: (attached: boolean) => void;
  onSaved: (tool: ToolOut) => void;
  onDeleted: () => void;
  secretBagSpec: ProviderSpec | undefined;
}) {
  const deleteTool = useDeleteTool();
  const gate = useWriteGate();

  const editTrigger = (
    <Button type="button" variant="ghost" size="icon-sm" aria-label="Edit" disabled={gate.pending}>
      <PencilIcon aria-hidden="true" />
    </Button>
  );

  return (
    <div className="flex min-h-14 flex-wrap items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 truncate font-mono text-body">{tool.name}</span>
          {!tool.enabled ? <StatusPill tone="neutral">Disabled</StatusPill> : null}
          {tool.agent_id === null ? <Tag>Shared</Tag> : null}
          {tool.definition.kind === "mcp" ? <McpAuthTag auth={tool.definition.auth} /> : null}
        </div>
        <p className="truncate text-caption text-text-secondary">{requestSummary(tool)}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <label className="mr-2 flex items-center gap-1.5 text-caption text-text-secondary">
          Use in this agent
          <Switch checked={attached} onCheckedChange={onToggleAttach} />
        </label>
        {tool.definition.kind === "http" ? (
          <DryRunDialog
            toolId={tool.id}
            trigger={
              <Button type="button" variant="ghost" size="icon-sm" aria-label="Dry run">
                <PlayIcon aria-hidden="true" />
              </Button>
            }
          />
        ) : null}
        {!gate.show ? null : tool.definition.kind === "http" ? (
          <HttpToolEditorDialog agentId={agentId} tool={tool} secretBagSpec={secretBagSpec} onSaved={onSaved} trigger={editTrigger} />
        ) : tool.kind === "provider" ? (
          // R-V5-8, V5-50: a `ProviderToolDefinition` gets its own dialog, never the MCP one.
          <ProviderToolEditorDialog
            tool={tool as ToolOut & { definition: ProviderToolDefinition }}
            onSaved={onSaved}
            trigger={editTrigger}
          />
        ) : tool.kind === "dataset" ? (
          // V6-19: a lookup-table tool (`DatasetToolDefinition`, D-V6-27) — its own editor, never the MCP one.
          <DatasetToolEditorDialog agentId={agentId} tool={tool} onSaved={onSaved} trigger={editTrigger} />
        ) : (
          <McpToolEditorDialog agentId={agentId} tool={tool} secretBagSpec={secretBagSpec} onSaved={onSaved} trigger={editTrigger} />
        )}
        {gate.show ? (
          <ConfirmDialog
            trigger={
              <Button type="button" variant="ghost" size="icon-sm" aria-label="Delete" disabled={gate.pending}>
                <Trash2Icon aria-hidden="true" />
              </Button>
            }
            title={`Delete “${tool.name}”?`}
            description="This removes the tool everywhere it's attached."
            confirmLabel="Delete tool"
            onConfirm={async () => {
              try {
                await deleteTool.mutateAsync(tool.id);
                toast.success(`${tool.name} deleted.`);
                onDeleted();
              } catch (error) {
                toast.error(errorMessage(error));
              }
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
