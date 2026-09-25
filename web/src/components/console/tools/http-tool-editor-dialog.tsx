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
import type { HttpToolDefinition, ProviderSpec, ToolExecution, ToolOut } from "@/contracts/lkap-contracts";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
const METHODS: HttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

/**
 * Background-tool execution policy. The contract's `mode`/`cancellable`/
 * `on_duplicate` are nullable ("agent decides"); the editor spells each out
 * as an explicit "Default" option rather than guessing a value, so an
 * untouched tool keeps posting `null` and a GET tool keeps inheriting the
 * agent's "Read tools run" setting (a POST/PUT/PATCH/DELETE tool still always
 * blocks unless a mode is chosen explicitly).
 */
type ModeDraft = "default" | "blocking" | "background" | "auto";
type CancellableDraft = "default" | "true" | "false";
type DuplicateDraft = "default" | "allow" | "reject" | "replace" | "confirm";

interface ExecutionDraft {
  mode: ModeDraft;
  announce: string;
  auto_threshold_ms: number;
  /** One filler phrase per line; ≤ 5 lines kept (`ToolExecution.fillers`, `max_length=5`). */
  fillersText: string;
  filler_delay_s: number;
  filler_interval_s: number;
  cancellable: CancellableDraft;
  on_duplicate: DuplicateDraft;
  max_duration_s: number;
}

const DEFAULT_EXECUTION_DRAFT: ExecutionDraft = {
  mode: "default",
  announce: "",
  auto_threshold_ms: 700,
  fillersText: "",
  filler_delay_s: 4,
  filler_interval_s: 8,
  cancellable: "default",
  on_duplicate: "default",
  max_duration_s: 60,
};

function fillersFromText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 5);
}

function executionDraftFromValue(execution: ToolExecution | undefined): ExecutionDraft {
  if (!execution) return DEFAULT_EXECUTION_DRAFT;
  return {
    mode: execution.mode ?? "default",
    announce: execution.announce ?? "",
    auto_threshold_ms: execution.auto_threshold_ms ?? 700,
    fillersText: (execution.fillers ?? []).join("\n"),
    filler_delay_s: execution.filler_delay_s ?? 4,
    filler_interval_s: execution.filler_interval_s ?? 8,
    cancellable: execution.cancellable === null || execution.cancellable === undefined
      ? "default"
      : execution.cancellable
        ? "true"
        : "false",
    on_duplicate: execution.on_duplicate ?? "default",
    max_duration_s: execution.max_duration_s ?? 60,
  };
}

function executionFromDraft(draft: ExecutionDraft): ToolExecution {
  return {
    mode: draft.mode === "default" ? null : draft.mode,
    announce: draft.announce.trim() === "" ? null : draft.announce,
    auto_threshold_ms: draft.auto_threshold_ms,
    fillers: fillersFromText(draft.fillersText) as ToolExecution["fillers"],
    filler_delay_s: draft.filler_delay_s,
    filler_interval_s: draft.filler_interval_s,
    cancellable: draft.cancellable === "default" ? null : draft.cancellable === "true",
    on_duplicate: draft.on_duplicate === "default" ? null : draft.on_duplicate,
    duplicate_scope: "name_and_args",
    max_duration_s: draft.max_duration_s,
  };
}

/** An explicit choice of "In the background" or "Automatic" — not "Default" (inherits the agent setting) or "Blocking". */
function isNonBlocking(mode: ModeDraft): boolean {
  return mode === "background" || mode === "auto";
}

/** The api validator's exact wording (`config_service.py::tool_execution_issues`). */
function silentReplyConflictMessage(name: string): string {
  return `'${name || "this tool"}' has silent_reply on, which would swallow its background announcement; turn one of them off`;
}

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
                  <Field label="Description (shown to the model)" htmlFor={`${uid}-description`}>
                    <Textarea
                      id={`${uid}-description`}
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
                <Field
                  label="Runs"
                  htmlFor={`${uid}-execution-mode`}
                  error={executionConflictMessage}
                  hint={
                    isRead
                      ? 'Left at "Agent default", this follows the agent\'s "Read tools run" setting (Instructions & voice → Conversation).'
                      : 'This tool changes something, so "Agent default" always blocks; choose a mode below to change that.'
                  }
                >
                  <Select
                    value={draft.execution.mode}
                    onValueChange={(v) =>
                      setDraft((d) => ({ ...d, execution: { ...d.execution, mode: v as ModeDraft } }))
                    }
                  >
                    <SelectTrigger id={`${uid}-execution-mode`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="default">Agent default</SelectItem>
                      <SelectItem value="blocking">Blocking</SelectItem>
                      <SelectItem value="background">In the background</SelectItem>
                      <SelectItem value="auto">Automatic</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
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
              <p className="text-[0.8125rem] text-muted-foreground">
                How this tool behaves while it runs. &quot;Blocking&quot; waits for the result before the agent
                replies; the other modes let the agent keep talking.
              </p>
              {!isRead ? (
                <p className="text-[0.8125rem] text-muted-foreground">
                  This tool changes something; the agent asks before running it twice.
                </p>
              ) : null}
              {isNonBlocking(draft.execution.mode) ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field
                    label="What the agent says first"
                    htmlFor={`${uid}-execution-announce`}
                    optional
                    hint='Its own words; default "Working on <name>."'
                  >
                    <Input
                      id={`${uid}-execution-announce`}
                      value={draft.execution.announce}
                      onChange={(e) =>
                        setDraft((d) => ({ ...d, execution: { ...d.execution, announce: e.target.value } }))
                      }
                      placeholder="Fetching that now."
                    />
                  </Field>
                  {draft.execution.mode === "auto" ? (
                    <Field
                      label="Switches to background after"
                      htmlFor={`${uid}-execution-threshold`}
                      hint="Milliseconds."
                    >
                      <Input
                        id={`${uid}-execution-threshold`}
                        type="number"
                        inputMode="numeric"
                        value={draft.execution.auto_threshold_ms}
                        onChange={(e) =>
                          setDraft((d) => ({
                            ...d,
                            execution: { ...d.execution, auto_threshold_ms: Number(e.target.value) },
                          }))
                        }
                      />
                    </Field>
                  ) : null}
                  <Field
                    label="Can be cancelled"
                    htmlFor={`${uid}-execution-cancellable`}
                    hint="Default: read tools can be cancelled; tools that change something can't."
                  >
                    <Select
                      value={draft.execution.cancellable}
                      onValueChange={(v) =>
                        setDraft((d) => ({
                          ...d,
                          execution: { ...d.execution, cancellable: v as ExecutionDraft["cancellable"] },
                        }))
                      }
                    >
                      <SelectTrigger id={`${uid}-execution-cancellable`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="default">Default</SelectItem>
                        <SelectItem value="true">Yes</SelectItem>
                        <SelectItem value="false">No</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field
                    label="Repeated calls"
                    htmlFor={`${uid}-execution-duplicate`}
                    hint="Default: reject a repeat of a read tool already running; ask again before repeating anything that changes something."
                  >
                    <Select
                      value={draft.execution.on_duplicate}
                      onValueChange={(v) =>
                        setDraft((d) => ({
                          ...d,
                          execution: { ...d.execution, on_duplicate: v as ExecutionDraft["on_duplicate"] },
                        }))
                      }
                    >
                      <SelectTrigger id={`${uid}-execution-duplicate`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="default">Default</SelectItem>
                        <SelectItem value="reject">Reject the repeat</SelectItem>
                        <SelectItem value="confirm">Ask again to confirm</SelectItem>
                        <SelectItem value="replace">Replace the running call</SelectItem>
                        <SelectItem value="allow">Allow it</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field label="Give up after" htmlFor={`${uid}-execution-max-duration`} hint="Seconds.">
                    <Input
                      id={`${uid}-execution-max-duration`}
                      type="number"
                      inputMode="numeric"
                      value={draft.execution.max_duration_s}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          execution: { ...d.execution, max_duration_s: Number(e.target.value) },
                        }))
                      }
                    />
                  </Field>
                </div>
              ) : null}
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <Field
                    label="Fillers while waiting"
                    htmlFor={`${uid}-execution-fillers`}
                    optional
                    hint="One phrase per line, up to five. Spoken as written; needs a voice."
                  >
                    <Textarea
                      id={`${uid}-execution-fillers`}
                      rows={3}
                      className="text-sm"
                      value={draft.execution.fillersText}
                      onChange={(e) =>
                        setDraft((d) => ({ ...d, execution: { ...d.execution, fillersText: e.target.value } }))
                      }
                      placeholder={"Still checking.\nAlmost there."}
                    />
                  </Field>
                </div>
                <Field label="First filler after" htmlFor={`${uid}-execution-filler-delay`} hint="Seconds.">
                  <Input
                    id={`${uid}-execution-filler-delay`}
                    type="number"
                    inputMode="numeric"
                    value={draft.execution.filler_delay_s}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, execution: { ...d.execution, filler_delay_s: Number(e.target.value) } }))
                    }
                  />
                </Field>
                <Field label="Then every" htmlFor={`${uid}-execution-filler-interval`} hint="Seconds.">
                  <Input
                    id={`${uid}-execution-filler-interval`}
                    type="number"
                    inputMode="numeric"
                    value={draft.execution.filler_interval_s}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        execution: { ...d.execution, filler_interval_s: Number(e.target.value) },
                      }))
                    }
                  />
                </Field>
              </div>
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
