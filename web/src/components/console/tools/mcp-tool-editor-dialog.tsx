"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useCreateTool, useUpdateTool } from "@/components/console/lib/api-hooks";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { McpServerDefinition, ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";

interface Draft {
  name: string;
  url: string;
  headersJson: string;
  credential_id: string | null;
  allowed_tools: string;
  timeout_s: number;
  sse_read_timeout_s: number;
  enabled: boolean;
}

function draftFromTool(tool: ToolOut | undefined): Draft {
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

interface DraftErrors {
  name?: string;
  url?: string;
  headersJson?: string;
}

/**
 * "MCP editor" (IMPLEMENTATION_PLAN W1-WEB-CONSOLE) — a streamable-HTTP MCP
 * server (docs/CONTRACTS.md §9), now a `Sheet` (docs/UI_UX_SPEC.md §7.6 item
 * 4) with inline validation instead of toasts. `agentId: null` attaches
 * nothing — used by the shared `/console/tools` list.
 */
export function McpToolEditorDialog({
  agentId,
  tool,
  secretBagSpec,
  trigger,
  onSaved,
}: {
  agentId: string | null;
  tool?: ToolOut;
  secretBagSpec: ProviderSpec | undefined;
  trigger: React.ReactNode;
  onSaved: (tool: ToolOut) => void;
}) {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState(() => draftFromTool(tool));
  const [errors, setErrors] = React.useState<DraftErrors>({});
  const createTool = useCreateTool();
  const updateTool = useUpdateTool();

  React.useEffect(() => {
    if (open) {
      setDraft(draftFromTool(tool));
      setErrors({});
    }
  }, [open, tool]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();

    const nextErrors: DraftErrors = {};

    let headers: Record<string, string> | undefined;
    try {
      headers = draft.headersJson.trim() === "" ? {} : (JSON.parse(draft.headersJson) as Record<string, string>);
    } catch {
      nextErrors.headersJson = "Must be valid JSON.";
    }
    if (draft.name.trim() === "") nextErrors.name = "Name is required.";
    if (draft.url.trim() === "") nextErrors.url = "URL is required.";

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0 || !headers) return;

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
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent
        side="right"
        className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-lg"
        aria-describedby={`${uid}-description`}
      >
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <SheetHeader className="border-b border-border px-5 py-4 pr-12">
            <SheetTitle className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em]">
              {tool ? "Edit MCP server" : "New MCP server"}
            </SheetTitle>
            <SheetDescription id={`${uid}-description`}>
              A streamable-HTTP MCP endpoint whose tools become available to the model.
            </SheetDescription>
          </SheetHeader>

          <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 py-5">
            <Field label="Name" htmlFor={`${uid}-name`} required error={errors.name}>
              <Input id={`${uid}-name`} value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} />
            </Field>
            <Field label="URL" htmlFor={`${uid}-url`} required error={errors.url}>
              <Input
                id={`${uid}-url`}
                className="font-mono text-sm"
                value={draft.url}
                onChange={(e) => setDraft((d) => ({ ...d, url: e.target.value }))}
                placeholder="https://mcp.example.com/stream"
              />
            </Field>
            <Field
              label="Headers"
              htmlFor={`${uid}-headers`}
              hint={"JSON; values may use {{ secret.NAME }}."}
              error={errors.headersJson}
            >
              <Textarea
                id={`${uid}-headers`}
                className="min-h-20 font-mono text-xs"
                value={draft.headersJson}
                onChange={(e) => setDraft((d) => ({ ...d, headersJson: e.target.value }))}
              />
            </Field>
            {secretBagSpec ? (
              <CredentialPicker
                spec={secretBagSpec}
                value={draft.credential_id}
                onChange={(id) => setDraft((d) => ({ ...d, credential_id: id }))}
              />
            ) : null}
            <Field
              label="Allowed tools"
              htmlFor={`${uid}-allowed-tools`}
              optional
              hint="Comma-separated; blank allows every tool the server exposes."
            >
              <Input
                id={`${uid}-allowed-tools`}
                value={draft.allowed_tools}
                onChange={(e) => setDraft((d) => ({ ...d, allowed_tools: e.target.value }))}
              />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Timeout" htmlFor={`${uid}-timeout`} hint="Seconds.">
                <Input
                  id={`${uid}-timeout`}
                  type="number"
                  inputMode="numeric"
                  value={draft.timeout_s}
                  onChange={(e) => setDraft((d) => ({ ...d, timeout_s: Number(e.target.value) }))}
                />
              </Field>
              <Field label="SSE read timeout" htmlFor={`${uid}-sse-timeout`} hint="Seconds.">
                <Input
                  id={`${uid}-sse-timeout`}
                  type="number"
                  inputMode="numeric"
                  value={draft.sse_read_timeout_s}
                  onChange={(e) => setDraft((d) => ({ ...d, sse_read_timeout_s: Number(e.target.value) }))}
                />
              </Field>
            </div>
            <Field inline label="Enabled" htmlFor={`${uid}-enabled`}>
              <Switch
                id={`${uid}-enabled`}
                checked={draft.enabled}
                onCheckedChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
              />
            </Field>
          </div>

          <SheetFooter className="flex-row justify-end border-t border-border px-5 py-4">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Saving…" : "Save server"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
