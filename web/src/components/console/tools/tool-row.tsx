"use client";

import * as React from "react";
import { toast } from "sonner";
import { PencilIcon, PlayIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { StatusChip } from "@/components/shared/status-chip";
import { useDeleteTool } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { DryRunDialog } from "@/components/console/tools/dry-run-dialog";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { ProviderToolEditorDialog } from "@/components/console/tools/provider-tool-editor-dialog";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import type { ProviderSpec, ProviderToolDefinition, ToolOut } from "@/contracts/lkap-contracts";

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
  const { canWrite } = useWriteAccess();
  const writeReason = writeAccessReason();

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-sm">{tool.name}</span>
          {!tool.enabled ? <StatusChip tone="neutral">disabled</StatusChip> : null}
          {tool.agent_id === null ? <StatusChip tone="info">shared</StatusChip> : null}
        </div>
        <p className="truncate text-xs text-muted-foreground">{requestSummary(tool)}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <label className="mr-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          Use in this agent
          <Switch checked={attached} onCheckedChange={onToggleAttach} />
        </label>
        {tool.definition.kind === "http" ? (
          <DryRunDialog
            toolId={tool.id}
            trigger={
              <Button type="button" variant="ghost" size="icon-sm" aria-label="Dry run">
                <PlayIcon className="size-3.5" />
              </Button>
            }
          />
        ) : null}
        {tool.definition.kind === "http" ? (
          <HttpToolEditorDialog
            agentId={agentId}
            tool={tool}
            secretBagSpec={secretBagSpec}
            onSaved={onSaved}
            trigger={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Edit"
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
            onSaved={onSaved}
            trigger={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Edit"
                disabled={!canWrite}
                title={canWrite ? undefined : writeReason}
              >
                <PencilIcon className="size-3.5" />
              </Button>
            }
          />
        ) : (
          <McpToolEditorDialog
            agentId={agentId}
            tool={tool}
            secretBagSpec={secretBagSpec}
            onSaved={onSaved}
            trigger={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Edit"
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
              aria-label="Delete"
              disabled={!canWrite}
              title={canWrite ? undefined : writeReason}
            >
              <Trash2Icon className="size-3.5" />
            </Button>
          }
          title={`Delete "${tool.name}"?`}
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
      </div>
    </div>
  );
}
