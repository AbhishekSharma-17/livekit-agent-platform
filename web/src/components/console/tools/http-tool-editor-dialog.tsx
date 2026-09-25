"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
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
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateTool, useUpdateTool } from "@/components/console/lib/api-hooks";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { errorMessage } from "@/components/console/shared/error-banner";
import {
  ExecutionFields,
  RunsField,
  executionDraftFromValue,
  executionFromDraft,
  isNonBlocking,
  silentReplyConflictMessage,
  type ExecutionDraft,
} from "@/components/console/tools/execution-fields";
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

interface Draft {
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
  execution: ExecutionDraft;
}

function draftFromTool(tool: ToolOut | undefined): Draft {
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
    execution: executionDraftFromValue(def?.execution),
  };
}

interface DraftErrors {
  name?: string;
  parametersJson?: string;
  headersJson?: string;
  allowed_hosts?: string;
  execution?: string;
}

/**
 * "HTTP tool editor with JSON Schema textarea + dry-run" (IMPLEMENTATION_PLAN
 * W1-WEB-CONSOLE), now a `Dialog` (a modal: no side drawers, UI_UX_SPEC-V2-AMENDMENTS §5) with sections (docs/UI_UX_SPEC.md §7.6 item
 * 4: Basics, Request, Auth, Response, Safety) and inline validation instead
 * of toasts. Builds an `HttpToolDefinition` (docs/CONTRACTS.md §9) and
 * posts/updates the `tools` row; the caller attaches the returned id to
 * `AgentConfig.tools.tool_ids`. `agentId: null` attaches nothing — used by
 * the shared `/console/tools` list.
 */
export function HttpToolEditorDialog({
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
  // Binding a credential needs `admin` server-side (REVIEW-V2 R2-03,
  // docs/v2/_asks.md V2-21-2) — stricter than the `builder` floor for the
  // rest of the tool editor.
  const { canWrite: canBindCredential } = useWriteAccess("admin");

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

    let parameters: Record<string, unknown> | undefined;
    try {
      parameters = JSON.parse(draft.parametersJson) as Record<string, unknown>;
    } catch {
      nextErrors.parametersJson = "Must be valid JSON Schema.";
    }

    let headers: Record<string, string> | undefined;
    try {
      headers = draft.headersJson.trim() === "" ? {} : (JSON.parse(draft.headersJson) as Record<string, string>);
    } catch {
      nextErrors.headersJson = "Must be valid JSON.";
    }

    if (!/^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/.test(draft.name)) {
      nextErrors.name = "Use letters, numbers or underscore; start with a letter or underscore.";
    }

    const allowedHosts = draft.allowed_hosts
      .split(",")
      .map((h) => h.trim())
      .filter(Boolean);
    if (allowedHosts.length === 0) {
      nextErrors.allowed_hosts = "Required — an empty list blocks every call.";
    }

    if (draft.silent_reply && isNonBlocking(draft.execution.mode)) {
      nextErrors.execution = silentReplyConflictMessage(draft.name);
    }

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0 || !parameters || !headers) return;

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
      execution: executionFromDraft(draft.execution),
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
  // Live, not just on submit (BACKGROUND-TOOLS.md §7): flipping either control disables Save at once.
  const executionConflict = draft.silent_reply && isNonBlocking(draft.execution.mode);
  const executionConflictMessage = executionConflict ? silentReplyConflictMessage(draft.name) : undefined;
  const isRead = draft.method === "GET";

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="lg" aria-describedby={`${uid}-description`}>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>
              {tool ? "Edit HTTP tool" : "New HTTP tool"}
            </DialogTitle>
            <DialogDescription id={`${uid}-description`}>
              Exposed to the model as a function tool. Arguments are validated against the JSON Schema below.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="gap-6">
            <section className="flex flex-col gap-4">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Basics</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Name" htmlFor={`${uid}-name`} required error={errors.name}>
                  <Input
                    id={`${uid}-name`}
                    className="font-mono text-sm"
                    value={draft.name}
                    onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                    placeholder="lookup_weather"
                  />
                </Field>
                <Field inline label="Enabled" htmlFor={`${uid}-enabled`}>
                  <Switch
                    id={`${uid}-enabled`}
                    checked={draft.enabled}
                    onCheckedChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
                  />
                </Field>
                <div className="sm:col-span-2">
                  {/* `${uid}-description-field`, not `${uid}-description` — that id is the
                      dialog's own `DialogDescription` (`aria-describedby`), and a duplicate
                      id would make this field's label bind to the wrong (non-labellable)
                      element. */}
                  <Field label="Description (shown to the model)" htmlFor={`${uid}-description-field`}>
                    <Textarea
                      id={`${uid}-description-field`}
                      rows={2}
                      value={draft.description}
                      onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                    />
                  </Field>
                </div>
              </div>
            </section>

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Request</h3>
              <Field label="Method" htmlFor={`${uid}-method`}>
                <Select value={draft.method} onValueChange={(v) => setDraft((d) => ({ ...d, method: v as HttpMethod }))}>
                  <SelectTrigger id={`${uid}-method`} className="w-full sm:w-48">
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
              </Field>
              <Field label="URL template" htmlFor={`${uid}-url`}>
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
              </Field>
              <Field label="Parameters (JSON Schema)" htmlFor={`${uid}-parameters`} error={errors.parametersJson}>
                <Textarea
                  id={`${uid}-parameters`}
                  className="min-h-32 font-mono text-xs"
                  value={draft.parametersJson}
                  onChange={(e) => setDraft((d) => ({ ...d, parametersJson: e.target.value }))}
                />
              </Field>
              <Field
                label="Body template"
                htmlFor={`${uid}-body-template`}
                optional
                hint={"JSON with {{ arg }} placeholders. Default: JSON of all arguments."}
              >
                <Textarea
                  id={`${uid}-body-template`}
                  className="min-h-20 font-mono text-xs"
                  value={draft.body_template}
                  onChange={(e) => setDraft((d) => ({ ...d, body_template: e.target.value }))}
                />
              </Field>
            </section>

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Auth</h3>
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
                <div
                  className={canBindCredential ? undefined : "pointer-events-none opacity-50"}
                  aria-disabled={!canBindCredential}
                  title={canBindCredential ? undefined : writeAccessReason("admin")}
                >
                  <CredentialPicker
                    spec={secretBagSpec}
                    value={draft.credential_id}
                    onChange={(id) => setDraft((d) => ({ ...d, credential_id: id }))}
                  />
                </div>
              ) : null}
            </section>

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Response</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="Result JSON pointer"
                  htmlFor={`${uid}-result-path`}
                  optional
                  hint="Narrows the result to a nested field."
                >
                  <Input
                    id={`${uid}-result-path`}
                    className="font-mono text-sm"
                    value={draft.result_path}
                    onChange={(e) => setDraft((d) => ({ ...d, result_path: e.target.value }))}
                    placeholder="/data/summary"
                  />
                </Field>
                <Field label="Max result characters" htmlFor={`${uid}-max-result-chars`}>
                  <Input
                    id={`${uid}-max-result-chars`}
                    type="number"
                    inputMode="numeric"
                    value={draft.max_result_chars}
                    onChange={(e) => setDraft((d) => ({ ...d, max_result_chars: Number(e.target.value) }))}
                  />
                </Field>
                <RunsField
                  uid={uid}
                  mode={draft.execution.mode}
                  onChange={(mode) => setDraft((d) => ({ ...d, execution: { ...d.execution, mode } }))}
                  isRead={isRead}
                  errorMessage={executionConflictMessage}
                />
                <Field
                  inline
                  label="Silent reply"
                  htmlFor={`${uid}-silent-reply`}
                  hint="Realtime mode only."
                  error={executionConflictMessage}
                >
                  <Switch
                    id={`${uid}-silent-reply`}
                    checked={draft.silent_reply}
                    onCheckedChange={(v) => setDraft((d) => ({ ...d, silent_reply: v }))}
                  />
                </Field>
              </div>
            </section>

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Execution</h3>
              <ExecutionFields
                uid={uid}
                draft={draft.execution}
                onChange={(execution) => setDraft((d) => ({ ...d, execution }))}
                isRead={isRead}
              />
            </section>

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Safety</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="Allowed hosts"
                  htmlFor={`${uid}-allowed-hosts`}
                  required
                  hint="Hosts the worker may call for this tool, comma-separated."
                  error={errors.allowed_hosts}
                >
                  <Input
                    id={`${uid}-allowed-hosts`}
                    value={draft.allowed_hosts}
                    onChange={(e) => setDraft((d) => ({ ...d, allowed_hosts: e.target.value }))}
                    placeholder="api.example.com"
                    required
                    aria-required="true"
                  />
                </Field>
                <Field label="Timeout" htmlFor={`${uid}-timeout`} hint="Seconds.">
                  <Input
                    id={`${uid}-timeout`}
                    type="number"
                    inputMode="numeric"
                    value={draft.timeout_s}
                    onChange={(e) => setDraft((d) => ({ ...d, timeout_s: Number(e.target.value) }))}
                  />
                </Field>
              </div>
            </section>
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending || executionConflict}>
              {pending ? "Saving…" : "Save tool"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
