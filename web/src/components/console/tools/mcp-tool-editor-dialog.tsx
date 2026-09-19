"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateTool, useUpdateTool } from "@/components/console/lib/api-hooks";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { McpServerDefinition, ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";

function draftFromTool(tool: ToolOut | undefined) {
  const def = tool?.definition.kind === "mcp" ? tool.definition : undefined;
  return {
    name: tool?.name ?? "",
    url: def?.url ?? "",
    headersJson: JSON.stringify(def?.headers ?? {}, null, 2),
    credential_id: def?.credential_id ?? null,
    allowed_tools: (def?.allowed_tools ?? []).join(", "),
    timeout_s: def?.timeout_s ?? 5,
    sse_read_timeout_s: def?.sse_read_timeout_s ?? 300,
    enabled: tool?.enabled ?? true,
  };
}

/** "MCP editor" (IMPLEMENTATION_PLAN W1-WEB-CONSOLE) — a streamable-HTTP MCP server (docs/CONTRACTS.md §9). */
export function McpToolEditorDialog({
  agentId,
  tool,
  secretBagSpec,
  trigger,
  onSaved,
}: {
  agentId: string;
  tool?: ToolOut;
  secretBagSpec: ProviderSpec | undefined;
  trigger: React.ReactNode;
  onSaved: (tool: ToolOut) => void;
}) {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState(() => draftFromTool(tool));
  const createTool = useCreateTool();
  const updateTool = useUpdateTool();

  React.useEffect(() => {
    if (open) setDraft(draftFromTool(tool));
  }, [open, tool]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();

    let headers: Record<string, string>;
    try {
      headers = draft.headersJson.trim() === "" ? {} : (JSON.parse(draft.headersJson) as Record<string, string>);
    } catch {
      toast.error("Headers must be valid JSON.");
      return;
    }
    if (draft.name.trim() === "" || draft.url.trim() === "") {
      toast.error("Name and URL are required.");
      return;
    }

    const allowedTools = draft.allowed_tools
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    const definition: McpServerDefinition = {
      kind: "mcp",
      name: draft.name,
      url: draft.url,
      headers,
      credential_id: draft.credential_id,
      allowed_tools: allowedTools.length > 0 ? allowedTools : null,
      timeout_s: draft.timeout_s,
      sse_read_timeout_s: draft.sse_read_timeout_s,
    };

    try {
      const saved = tool
        ? await updateTool.mutateAsync({
            id: tool.id,
            body: { agent_id: agentId, kind: "mcp", name: draft.name, definition, enabled: draft.enabled },
          })
        : await createTool.mutateAsync({
            agent_id: agentId,
            kind: "mcp",
            name: draft.name,
            definition,
            enabled: draft.enabled,
          });
      setOpen(false);
      toast.success(`MCP server "${saved.name}" saved.`);
      onSaved(saved);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const pending = createTool.isPending || updateTool.isPending;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{tool ? "Edit MCP server" : "New MCP server"}</DialogTitle>
            <DialogDescription>A streamable-HTTP MCP endpoint whose tools become available to the model.</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div>
              <label htmlFor={`${uid}-name`} className="mb-1 block text-sm font-medium">
                Name
              </label>
              <Input
                id={`${uid}-name`}
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              />
            </div>
            <div>
              <label htmlFor={`${uid}-url`} className="mb-1 block text-sm font-medium">
                URL
              </label>
              <Input
                id={`${uid}-url`}
                className="font-mono text-sm"
                value={draft.url}
                onChange={(e) => setDraft((d) => ({ ...d, url: e.target.value }))}
                placeholder="https://mcp.example.com/stream"
              />
            </div>
            <div>
              <label htmlFor={`${uid}-headers`} className="mb-1 block text-sm font-medium">
                Headers (JSON; values may use {"{{ secret.NAME }}"})
              </label>
              <Textarea
                id={`${uid}-headers`}
                className="min-h-20 font-mono text-xs"
                value={draft.headersJson}
                onChange={(e) => setDraft((d) => ({ ...d, headersJson: e.target.value }))}
              />
            </div>
            {secretBagSpec ? (
              <CredentialPicker
                spec={secretBagSpec}
                value={draft.credential_id}
                onChange={(id) => setDraft((d) => ({ ...d, credential_id: id }))}
              />
            ) : null}
            <div>
              <label htmlFor={`${uid}-allowed-tools`} className="mb-1 block text-sm font-medium">
                Allowed tools (comma-separated; blank = all)
              </label>
              <Input
                id={`${uid}-allowed-tools`}
                value={draft.allowed_tools}
                onChange={(e) => setDraft((d) => ({ ...d, allowed_tools: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor={`${uid}-timeout`} className="mb-1 block text-sm font-medium">
                  Timeout (s)
                </label>
                <Input
                  id={`${uid}-timeout`}
                  type="number"
                  value={draft.timeout_s}
                  onChange={(e) => setDraft((d) => ({ ...d, timeout_s: Number(e.target.value) }))}
                />
              </div>
              <div>
                <label htmlFor={`${uid}-sse-timeout`} className="mb-1 block text-sm font-medium">
                  SSE read timeout (s)
                </label>
                <Input
                  id={`${uid}-sse-timeout`}
                  type="number"
                  value={draft.sse_read_timeout_s}
                  onChange={(e) => setDraft((d) => ({ ...d, sse_read_timeout_s: Number(e.target.value) }))}
                />
              </div>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <span className="text-sm font-medium">Enabled</span>
              <Switch checked={draft.enabled} onCheckedChange={(v) => setDraft((d) => ({ ...d, enabled: v }))} />
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Saving…" : "Save server"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
