"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import type { HttpToolDefinition, ProviderSpec, ToolOut } from "@/contracts/lkap-contracts";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
const METHODS: HttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

/**
 * Best-effort hostname extraction for pre-filling `allowed_hosts` from a URL
 * template. Template placeholders (`{{ arg }}`) anywhere in the host part
 * make the URL unparseable, in which case this returns `null` and the field
 * is left alone (F-05 / WP-C).
 */
function hostnameOf(url: string): string | null {
  try {
    return new URL(url).hostname || null;
  } catch {
    return null;
  }
}

function draftFromTool(tool: ToolOut | undefined): {
  name: string;
  description: string;
  method: HttpMethod;
  url: string;
  parametersJson: string;
  headersJson: string;
  credential_id: string | null;
  body_template: string;
  allowed_hosts: string;
  timeout_s: number;
  max_result_chars: number;
  result_path: string;
  silent_reply: boolean;
  enabled: boolean;
} {
  const def = tool?.definition.kind === "http" ? tool.definition : undefined;
  return {
    name: tool?.name ?? "",
    description: def?.description ?? "",
    method: def?.method ?? "POST",
    url: def?.url ?? "",
    parametersJson: JSON.stringify(def?.parameters ?? { type: "object", properties: {}, required: [] }, null, 2),
    headersJson: JSON.stringify(def?.headers ?? {}, null, 2),
    credential_id: def?.credential_id ?? null,
    body_template: def?.body_template ?? "",
    allowed_hosts: (def?.allowed_hosts ?? []).join(", "),
    timeout_s: def?.timeout_s ?? 10,
    max_result_chars: def?.max_result_chars ?? 4000,
    result_path: def?.result_path ?? "",
    silent_reply: def?.silent_reply ?? false,
    enabled: tool?.enabled ?? true,
  };
}

/**
 * "HTTP tool editor with JSON Schema textarea + dry-run" (IMPLEMENTATION_PLAN
 * W1-WEB-CONSOLE). Builds an `HttpToolDefinition` (docs/CONTRACTS.md §9) and
 * posts/updates the `tools` row; the caller attaches the returned id to
 * `AgentConfig.tools.tool_ids`.
 */
export function HttpToolEditorDialog({
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

    let parameters: Record<string, unknown>;
    let headers: Record<string, string>;
    try {
      parameters = JSON.parse(draft.parametersJson) as Record<string, unknown>;
    } catch {
      toast.error("Parameters must be valid JSON Schema.");
      return;
    }
    try {
      headers = draft.headersJson.trim() === "" ? {} : (JSON.parse(draft.headersJson) as Record<string, string>);
    } catch {
      toast.error("Headers must be valid JSON.");
      return;
    }
    if (!/^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/.test(draft.name)) {
      toast.error("Name must start with a letter/underscore and contain only letters, numbers, underscore.");
      return;
    }

    const allowedHosts = draft.allowed_hosts
      .split(",")
      .map((h) => h.trim())
      .filter(Boolean);
    if (allowedHosts.length === 0) {
      toast.error("Allowed hosts is required — an empty list blocks every call (F-05).");
      return;
    }

    const definition: HttpToolDefinition = {
      kind: "http",
      name: draft.name,
      description: draft.description,
      parameters,
      method: draft.method,
      url: draft.url,
      headers,
      credential_id: draft.credential_id,
      body_template: draft.body_template.trim() === "" ? null : draft.body_template,
      allowed_hosts: allowedHosts,
      timeout_s: draft.timeout_s,
      max_result_chars: draft.max_result_chars,
      result_path: draft.result_path.trim() === "" ? null : draft.result_path,
      silent_reply: draft.silent_reply,
    };

    try {
      const saved = tool
        ? await updateTool.mutateAsync({
            id: tool.id,
            body: { agent_id: agentId, kind: "http", name: draft.name, definition, enabled: draft.enabled },
          })
        : await createTool.mutateAsync({
            agent_id: agentId,
            kind: "http",
            name: draft.name,
            definition,
            enabled: draft.enabled,
          });
      setOpen(false);
      toast.success(`HTTP tool "${saved.name}" saved.`);
      onSaved(saved);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const pending = createTool.isPending || updateTool.isPending;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{tool ? "Edit HTTP tool" : "New HTTP tool"}</DialogTitle>
            <DialogDescription>
              Exposed to the model as a function tool. Arguments are validated against the JSON Schema below.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-2 sm:grid-cols-2">
            <div>
              <label htmlFor={`${uid}-name`} className="mb-1 block text-sm font-medium">
                Name
              </label>
              <Input
                id={`${uid}-name`}
                className="font-mono text-sm"
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                placeholder="lookup_weather"
              />
            </div>
            <div>
              <label htmlFor={`${uid}-method`} className="mb-1 block text-sm font-medium">
                Method
              </label>
              <Select value={draft.method} onValueChange={(v) => setDraft((d) => ({ ...d, method: v as HttpMethod }))}>
                <SelectTrigger id={`${uid}-method`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {METHODS.map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="sm:col-span-2">
              <label htmlFor={`${uid}-description`} className="mb-1 block text-sm font-medium">
                Description (shown to the model)
              </label>
              <Textarea
                id={`${uid}-description`}
                rows={2}
                value={draft.description}
                onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
              />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor={`${uid}-url`} className="mb-1 block text-sm font-medium">
                URL template
              </label>
              <Input
                id={`${uid}-url`}
                className="font-mono text-sm"
                value={draft.url}
                onChange={(e) => {
                  const url = e.target.value;
                  setDraft((d) => {
                    if (d.allowed_hosts.trim() !== "") return { ...d, url };
                    const host = hostnameOf(url);
                    return host ? { ...d, url, allowed_hosts: host } : { ...d, url };
                  });
                }}
                placeholder="https://api.example.com/items/{{ item_id }}"
              />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor={`${uid}-parameters`} className="mb-1 block text-sm font-medium">
                Parameters (JSON Schema)
              </label>
              <Textarea
                id={`${uid}-parameters`}
                className="min-h-32 font-mono text-xs"
                value={draft.parametersJson}
                onChange={(e) => setDraft((d) => ({ ...d, parametersJson: e.target.value }))}
              />
            </div>
            <div className="sm:col-span-2">
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
            <div className="sm:col-span-2">
              <label htmlFor={`${uid}-body-template`} className="mb-1 block text-sm font-medium">
                Body template (optional; JSON with {"{{ arg }}"})
              </label>
              <Textarea
                id={`${uid}-body-template`}
                className="min-h-20 font-mono text-xs"
                value={draft.body_template}
                onChange={(e) => setDraft((d) => ({ ...d, body_template: e.target.value }))}
                placeholder="Default: JSON of all arguments"
              />
            </div>
            {secretBagSpec ? (
              <div className="sm:col-span-2">
                <CredentialPicker
                  spec={secretBagSpec}
                  value={draft.credential_id}
                  onChange={(id) => setDraft((d) => ({ ...d, credential_id: id }))}
                />
              </div>
            ) : null}
            <div>
              <label htmlFor={`${uid}-allowed-hosts`} className="mb-1 block text-sm font-medium">
                Allowed hosts (comma-separated)
                <span className="ml-0.5 text-destructive">*</span>
              </label>
              <Input
                id={`${uid}-allowed-hosts`}
                value={draft.allowed_hosts}
                onChange={(e) => setDraft((d) => ({ ...d, allowed_hosts: e.target.value }))}
                placeholder="api.example.com"
                required
                aria-required="true"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Hosts the worker may call for this tool. Required: an empty list blocks every call.
              </p>
            </div>
            <div>
              <label htmlFor={`${uid}-result-path`} className="mb-1 block text-sm font-medium">
                Result JSON pointer (optional)
              </label>
              <Input
                id={`${uid}-result-path`}
                className="font-mono text-sm"
                value={draft.result_path}
                onChange={(e) => setDraft((d) => ({ ...d, result_path: e.target.value }))}
                placeholder="/data/summary"
              />
            </div>
            <div>
              <label htmlFor={`${uid}-timeout`} className="mb-1 block text-sm font-medium">
                Timeout (seconds)
              </label>
              <Input
                id={`${uid}-timeout`}
                type="number"
                value={draft.timeout_s}
                onChange={(e) => setDraft((d) => ({ ...d, timeout_s: Number(e.target.value) }))}
              />
            </div>
            <div>
              <label htmlFor={`${uid}-max-result-chars`} className="mb-1 block text-sm font-medium">
                Max result characters
              </label>
              <Input
                id={`${uid}-max-result-chars`}
                type="number"
                value={draft.max_result_chars}
                onChange={(e) => setDraft((d) => ({ ...d, max_result_chars: Number(e.target.value) }))}
              />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <span className="text-sm font-medium">Silent reply (realtime)</span>
              <Switch
                checked={draft.silent_reply}
                onCheckedChange={(v) => setDraft((d) => ({ ...d, silent_reply: v }))}
              />
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
              {pending ? "Saving…" : "Save tool"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
