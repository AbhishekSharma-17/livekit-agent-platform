"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
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
import {
  useToolProviderConnections,
  useUpdateTool,
  useRefreshProviderToolSchema,
  type ProviderToolSchemaRefreshOut,
} from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import {
  DEFAULT_EXECUTION_DRAFT,
  ExecutionFields,
  RunsField,
  executionDraftFromValue,
  executionFromDraft,
  isNonBlocking,
  silentReplyConflictMessage,
  type ExecutionDraft,
} from "@/components/console/tools/execution-fields";
import type { AppConnectionOut, ProviderToolDefinition, ToolOut } from "@/contracts/lkap-contracts";

/** Mirrors `connection-row.tsx`'s status mapping (docs/v5/COMPOSIO.md §6) for the header's read-only status chip. */
const STATUS_TONE: Record<AppConnectionOut["status"], StatusTone> = {
  active: "success",
  initiated: "info",
  expired: "warning",
  failed: "danger",
  inactive: "neutral",
  unknown: "neutral",
};

const STATUS_LABEL: Record<AppConnectionOut["status"], string> = {
  active: "Connected",
  initiated: "Waiting for sign-in…",
  expired: "Needs reconnect",
  failed: "Needs reconnect",
  inactive: "Needs reconnect",
  unknown: "Unknown",
};

/**
 * A human-readable stand-in for the provider's original action name — the
 * tool only carries `tool_slug` (docs/v5/COMPOSIO.md D-V5-C8), not a separate
 * display name, so this strips the toolkit prefix Composio slugs always
 * carry (`GOOGLECALENDAR_FIND_FREE_SLOTS` → "Find Free Slots") and falls back
 * to the raw slug, title-cased, when the prefix doesn't match. The raw slug
 * itself is always shown too (in the "Details" disclosure — copy rule: no
 * "slug" outside it).
 */
export function humanizeActionSlug(toolkit: string, slug: string): string {
  const prefix = `${toolkit.toUpperCase()}_`;
  const withoutPrefix = slug.toUpperCase().startsWith(prefix) ? slug.slice(prefix.length) : slug;
  return withoutPrefix
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

interface ParameterField {
  name: string;
  type: string;
  required: boolean;
  description?: string;
}

/** The pinned JSON Schema's top-level properties, for the read-only field list. */
function parameterFields(parameters: Record<string, unknown>): ParameterField[] {
  const properties = parameters.properties;
  if (!properties || typeof properties !== "object") return [];
  const required = new Set(Array.isArray(parameters.required) ? (parameters.required as unknown[]).map(String) : []);
  return Object.entries(properties as Record<string, unknown>).map(([name, def]) => {
    const schema = (def && typeof def === "object" ? (def as Record<string, unknown>) : {}) as Record<string, unknown>;
    return {
      name,
      type: typeof schema.type === "string" ? schema.type : "any",
      required: required.has(name),
      description: typeof schema.description === "string" ? schema.description : undefined,
    };
  });
}

interface Draft {
  name: string;
  description: string;
  timeout_s: number;
  max_result_chars: number;
  result_path: string;
  silent_reply: boolean;
  execution: ExecutionDraft;
}

function draftFromDefinition(definition: ProviderToolDefinition): Draft {
  return {
    name: definition.name,
    description: definition.description,
    timeout_s: definition.timeout_s ?? 10,
    max_result_chars: definition.max_result_chars ?? 1500,
    result_path: definition.result_path ?? "",
    silent_reply: definition.silent_reply ?? false,
    execution: executionDraftFromValue(definition.execution) ?? DEFAULT_EXECUTION_DRAFT,
  };
}

interface DraftErrors {
  name?: string;
  execution?: string;
}

/**
 * The editor for one app action (`ProviderToolDefinition`, R-V5-8; wired into
 * `tool-row.tsx`/`tools-list.tsx` where Edit was hidden for `kind ===
 * "provider"` rows since ask #46/R-V5-8's interim). Unlike the HTTP and MCP
 * editors this never creates a new tool — an app action only ever comes from
 * materialising a picked action (`ActionsDialog` → `POST .../materialise`) —
 * so `tool` is required, not optional.
 *
 * The read-only header (app identity, the action's original name and slug,
 * the connection's status) never changes; the editable fields are exactly
 * R-V5-8's list (name, description, timeout, max result characters, result
 * pointer, silent reply) plus the shared execution controls
 * (`execution-fields.tsx`, extracted from V4-13's HTTP editor) and the pinned
 * parameters with **Refresh schema**. Copy says "App action", never
 * "Composio action" or "provider".
 */
export function ProviderToolEditorDialog({
  tool,
  trigger,
  onSaved,
}: {
  tool: ToolOut & { definition: ProviderToolDefinition };
  trigger: React.ReactNode;
  onSaved: (tool: ToolOut) => void;
}) {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const definition = tool.definition;
  const [draft, setDraft] = React.useState(() => draftFromDefinition(definition));
  const [errors, setErrors] = React.useState<DraftErrors>({});
  const [refreshResult, setRefreshResult] = React.useState<ProviderToolSchemaRefreshOut | null>(null);
  const updateTool = useUpdateTool();
  const refreshSchema = useRefreshProviderToolSchema();
  const connectionsQuery = useToolProviderConnections();

  // Reset from the tool only when the dialog freshly opens — not on every
  // re-render while it's open, so a schema Apply's refetch (which changes
  // `tool.definition.parameters`, read directly below rather than mirrored
  // into `draft`) never clobbers an in-progress name/description edit.
  React.useEffect(() => {
    if (open) {
      setDraft(draftFromDefinition(tool.definition));
      setErrors({});
      setRefreshResult(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const connection = (connectionsQuery.data?.items ?? []).find((c) => c.id === definition.connection_id);
  const appName = connection?.toolkit_name ?? connection?.toolkit ?? definition.toolkit ?? "App";
  const status = connection?.status ?? "unknown";
  const needsReconnect = connection ? connection.needs_reconnect || connection.status !== "active" : true;
  const actionName = humanizeActionSlug(definition.toolkit ?? "", definition.tool_slug);
  const fields = parameterFields(definition.parameters);
  const isRead = definition.risk === "read";
  const lockBlocking = definition.risk === "destructive";

  async function handleRefresh(apply: boolean) {
    try {
      const result = await refreshSchema.mutateAsync({ toolId: tool.id, apply });
      setRefreshResult(result);
      if (apply) toast.success(result.changed ? "Schema updated." : "No changes to apply.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();

    const nextErrors: DraftErrors = {};
    if (!/^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/.test(draft.name)) {
      nextErrors.name = "Use letters, numbers or underscore; start with a letter or underscore.";
    }
    if (draft.silent_reply && isNonBlocking(draft.execution.mode)) {
      nextErrors.execution = silentReplyConflictMessage(draft.name);
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    // Spreads the live `tool.definition` (not a stale snapshot) so fields
    // this dialog never edits — `tool_slug`, `connection_id`, `subject`,
    // `credential_id`, `schema_version`, `risk`, and `parameters` (possibly
    // just refreshed) — are preserved exactly.
    const nextDefinition: ProviderToolDefinition = {
      ...tool.definition,
      name: draft.name,
      description: draft.description,
      timeout_s: draft.timeout_s,
      max_result_chars: draft.max_result_chars,
      result_path: draft.result_path.trim() === "" ? null : draft.result_path,
      silent_reply: draft.silent_reply,
      execution: executionFromDraft(draft.execution),
    };

    try {
      const saved = await updateTool.mutateAsync({
        id: tool.id,
        body: {
          agent_id: tool.agent_id ?? null,
          kind: "provider",
          name: draft.name,
          definition: nextDefinition,
          enabled: tool.enabled,
        },
      });
      setOpen(false);
      toast.success(`"${saved.name}" saved.`);
      onSaved(saved);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const executionConflict = draft.silent_reply && isNonBlocking(draft.execution.mode);
  const executionConflictMessage = executionConflict ? silentReplyConflictMessage(draft.name) : undefined;
  const pending = updateTool.isPending;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="lg" aria-describedby={`${uid}-description`}>
        <form onSubmit={(event) => void handleSubmit(event)} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>Edit App action</DialogTitle>
            <DialogDescription id={`${uid}-description`}>
              One action of a connected app, run as a tool. The name, description and how it runs are yours to
              change; the app and the action itself aren&rsquo;t.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="gap-6">
            <section className="flex flex-col gap-3 rounded-lg border border-border bg-muted/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <VendorMark vendor={appName} />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">{appName}</p>
                    <p className="truncate text-xs text-muted-foreground">{actionName}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <StatusChip tone={STATUS_TONE[status]} dot size="sm">
                    {STATUS_LABEL[status]}
                  </StatusChip>
                  <Link
                    href="/console/tools?tab=apps"
                    className={`text-xs underline underline-offset-2 ${needsReconnect ? "text-warning-text" : "text-muted-foreground"}`}
                  >
                    Tools → Apps
                  </Link>
                </div>
              </div>
              <Collapsible>
                <CollapsibleTrigger asChild>
                  <Button type="button" variant="ghost" size="sm" className="w-fit text-xs text-muted-foreground">
                    Details
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent className="flex flex-col gap-1 pt-1 text-xs text-muted-foreground">
                  <p>
                    Action id: <span className="font-mono">{definition.tool_slug}</span>
                  </p>
                  {definition.schema_version ? (
                    <p>
                      Schema version: <span className="font-mono">{definition.schema_version}</span>
                    </p>
                  ) : null}
                </CollapsibleContent>
              </Collapsible>
            </section>

            <section className="flex flex-col gap-4">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Basics</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Name" htmlFor={`${uid}-name`} required error={errors.name}>
                  <Input
                    id={`${uid}-name`}
                    className="font-mono text-sm"
                    value={draft.name}
                    onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                  />
                </Field>
                <div className="sm:col-span-2">
                  {/* `${uid}-description-field`, not `${uid}-description` — that id is the
                      dialog's own `DialogDescription` (`aria-describedby`). */}
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
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Parameters</h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={refreshSchema.isPending}
                  onClick={() => void handleRefresh(false)}
                >
                  {refreshSchema.isPending ? "Checking…" : "Refresh schema"}
                </Button>
              </div>
              {fields.length === 0 ? (
                <p className="text-[0.8125rem] text-muted-foreground">This action takes no arguments.</p>
              ) : (
                <ul className="flex flex-col gap-1 rounded-md border border-border p-2.5">
                  {fields.map((field) => (
                    <li key={field.name} className="flex flex-wrap items-baseline gap-x-2 text-[0.8125rem]">
                      <span className="font-mono text-foreground">{field.name}</span>
                      <span className="text-xs text-muted-foreground">{field.type}</span>
                      {field.required ? (
                        <StatusChip tone="neutral" size="sm">
                          Required
                        </StatusChip>
                      ) : null}
                      {field.description ? <span className="text-xs text-muted-foreground">— {field.description}</span> : null}
                    </li>
                  ))}
                </ul>
              )}
              {refreshResult ? (
                refreshResult.changed ? (
                  <div className="flex flex-col gap-2 rounded-md border border-border bg-warning-soft p-2.5 text-[0.8125rem]">
                    <p className="font-medium text-warning-text">The app changed this action since it was added.</p>
                    {refreshResult.added.length > 0 ? <p>Added: {refreshResult.added.join(", ")}</p> : null}
                    {refreshResult.removed.length > 0 ? <p>Removed: {refreshResult.removed.join(", ")}</p> : null}
                    {refreshResult.modified.length > 0 ? <p>Changed: {refreshResult.modified.join(", ")}</p> : null}
                    {!refreshResult.applied ? (
                      <Button
                        type="button"
                        size="sm"
                        className="w-fit"
                        disabled={refreshSchema.isPending}
                        onClick={() => void handleRefresh(true)}
                      >
                        Apply
                      </Button>
                    ) : (
                      <p className="text-muted-foreground">Applied.</p>
                    )}
                  </div>
                ) : (
                  <p className="text-[0.8125rem] text-muted-foreground">No changes since this action was added.</p>
                )
              ) : null}
            </section>

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Response</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <RunsField
                  uid={uid}
                  mode={draft.execution.mode}
                  onChange={(mode) => setDraft((d) => ({ ...d, execution: { ...d.execution, mode } }))}
                  isRead={isRead}
                  lockBlocking={lockBlocking}
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

            <section className="flex flex-col gap-4 border-t border-border pt-5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">Execution</h3>
              <ExecutionFields
                uid={uid}
                draft={draft.execution}
                onChange={(execution) => setDraft((d) => ({ ...d, execution }))}
                isRead={isRead}
              />
            </section>
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending || executionConflict}>
              {pending ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
