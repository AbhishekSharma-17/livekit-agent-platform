"use client";

import * as React from "react";
import { toast } from "sonner";
import { PencilIcon, PlayIcon, Trash2Icon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useDeleteTool } from "@/components/console/lib/api-hooks";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { DryRunDialog } from "@/components/console/tools/dry-run-dialog";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";

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
  const detail =
    tool.definition.kind === "http" ? `${tool.definition.method} ${tool.definition.url}` : tool.definition.url;

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-sm">{tool.name}</span>
          {!tool.enabled ? <Badge variant="secondary">disabled</Badge> : null}
          {tool.agent_id === null ? <Badge variant="secondary">shared</Badge> : null}
        </div>
        <p className="truncate text-xs text-muted-foreground">{detail}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <label className="mr-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          Attached
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
              <Button type="button" variant="ghost" size="icon-sm" aria-label="Edit">
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
              <Button type="button" variant="ghost" size="icon-sm" aria-label="Edit">
                <PencilIcon className="size-3.5" />
              </Button>
            }
          />
        )}
        <ConfirmDialog
          trigger={
            <Button type="button" variant="ghost" size="icon-sm" aria-label="Delete">
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
