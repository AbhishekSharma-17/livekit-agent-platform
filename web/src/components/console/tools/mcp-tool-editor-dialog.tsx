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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
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
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { McpServerDefinition, ProviderSpec, ToolExecution, ToolOut } from "@/contracts/lkap-contracts";

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
 * Per-tool execution options (BACKGROUND-TOOLS.md §7): one row per allowed
 * tool name (free text when `allowed_tools` is unset). Only rows the admin
 * has touched are posted in `tool_options` — an unedited row means "no
 * override" and stays out of the payload (D-V4-32's mechanical defaults
 * apply on the worker).
 */
type CancellableDraft = "default" | "true" | "false";
type DuplicateDraft = "default" | "allow" | "reject" | "replace" | "confirm";

interface McpOptionDraft {
  mode: "blocking" | "background" | "auto";
  report_progress: boolean;
  cancellable: CancellableDraft;
  on_duplicate: DuplicateDraft;
}

const DEFAULT_MCP_OPTION: McpOptionDraft = {
  mode: "blocking",
  report_progress: false,
  cancellable: "default",
  on_duplicate: "default",
};

function mcpOptionFromExecution(execution: ToolExecution | undefined): McpOptionDraft {
  if (!execution) return DEFAULT_MCP_OPTION;
  return {
    mode: execution.mode ?? "blocking",
    report_progress: execution.report_progress ?? false,
    cancellable:
      execution.cancellable === null || execution.cancellable === undefined
        ? "default"
        : execution.cancellable
          ? "true"
          : "false",
    on_duplicate: execution.on_duplicate ?? "default",
  };
}

function executionFromMcpOption(draft: McpOptionDraft): ToolExecution {
  return {
    mode: draft.mode,
    report_progress: draft.report_progress,
    cancellable: draft.cancellable === "default" ? null : draft.cancellable === "true",
    on_duplicate: draft.on_duplicate === "default" ? null : draft.on_duplicate,
    duplicate_scope: "name_and_args",
  };
}

/**
 * "MCP editor" (IMPLEMENTATION_PLAN W1-WEB-CONSOLE) — a streamable-HTTP MCP
 * server (docs/CONTRACTS.md §9), now a `Dialog` (a modal: no side drawers, UI_UX_SPEC-V2-AMENDMENTS §5) (docs/UI_UX_SPEC.md §7.6 item
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

  const existingOptions = tool?.definition.kind === "mcp" ? (tool.definition.tool_options ?? {}) : {};
  const [toolOptions, setToolOptions] = React.useState<Record<string, McpOptionDraft>>(() =>
    Object.fromEntries(Object.entries(existingOptions).map(([name, exec]) => [name, mcpOptionFromExecution(exec)])),
  );
  const [freeTextRow, setFreeTextRow] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setDraft(draftFromTool(tool));
      const options = tool?.definition.kind === "mcp" ? (tool.definition.tool_options ?? {}) : {};
      setToolOptions(
        Object.fromEntries(Object.entries(options).map(([name, exec]) => [name, mcpOptionFromExecution(exec)])),
      );
      setFreeTextRow("");
      setErrors({});
    }
  }, [open, tool]);

  const allowedToolNames = draft.allowed_tools
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  // Rows: the allowed names when the server restricts them; otherwise every name
  // an override has already been saved for, plus one free-text row to add another.
  const optionRows = allowedToolNames.length > 0 ? allowedToolNames : Object.keys(toolOptions);

  function optionFor(name: string): McpOptionDraft {
    return toolOptions[name] ?? DEFAULT_MCP_OPTION;
  }

  function patchOption(name: string, patch: Partial<McpOptionDraft>) {
    setToolOptions((current) => ({ ...current, [name]: { ...optionFor(name), ...patch } }));
  }

  function addFreeTextRow() {
    const name = freeTextRow.trim();
    if (!name || toolOptions[name]) return;
    setToolOptions((current) => ({ ...current, [name]: DEFAULT_MCP_OPTION }));
    setFreeTextRow("");
  }

  function removeRow(name: string) {
    setToolOptions((current) => {
      const next = { ...current };
      delete next[name];
      return next;
    });
  }

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
    // A stale free-text row from before `allowed_tools` was narrowed would
    // otherwise still post an override for a name the server rejects
    // ("not one of this server's allowed_tools").
    const validOptionNames = allowedTools.length > 0 ? new Set(allowedTools) : null;

    const definition: McpServerDefinition = {
      kind: "mcp",
      name: draft.name,
      url: draft.url,
      headers,
      credential_id: draft.credential_id,
      allowed_tools: allowedTools.length > 0 ? allowedTools : null,
      timeout_s: draft.timeout_s,
      sse_read_timeout_s: draft.sse_read_timeout_s,
      // Only rows the admin touched (D-V4-32: an unedited row means "no override").
      tool_options: Object.fromEntries(
        Object.entries(toolOptions)
          .filter(([name]) => !validOptionNames || validOptionNames.has(name))
          .map(([name, option]) => [name, executionFromMcpOption(option)]),
      ),
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
      <DialogContent size="lg" aria-describedby={`${uid}-description`}>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>
              {tool ? "Edit MCP server" : "New MCP server"}
            </DialogTitle>
            <DialogDescription id={`${uid}-description`}>
              A streamable-HTTP MCP endpoint whose tools become available to the model.
            </DialogDescription>
          </DialogHeader>

          <DialogBody>
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

            <div className="flex flex-col gap-2 border-t border-border pt-4">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">How each tool runs</h3>
              <p className="text-[0.8125rem] text-muted-foreground">
                Left at &quot;Blocking&quot; until edited — an MCP tool never runs in the background unless it opts in here.
              </p>
              {optionRows.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Tool</TableHead>
                      <TableHead>Runs</TableHead>
                      <TableHead>Announce progress</TableHead>
                      <TableHead>Can be cancelled</TableHead>
                      <TableHead>Repeated calls</TableHead>
                      {allowedToolNames.length === 0 ? <TableHead className="sr-only">Remove</TableHead> : null}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {optionRows.map((name) => {
                      const option = optionFor(name);
                      const rowId = `${uid}-mcp-option-${name}`;
                      return (
                        <TableRow key={name}>
                          <TableCell className="font-mono text-xs">{name}</TableCell>
                          <TableCell>
                            <Select value={option.mode} onValueChange={(v) => patchOption(name, { mode: v as McpOptionDraft["mode"] })}>
                              <SelectTrigger id={`${rowId}-mode`} className="w-36" aria-label={`${name} — runs`}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="blocking">Blocking</SelectItem>
                                <SelectItem value="background">In the background</SelectItem>
                                <SelectItem value="auto">Automatic</SelectItem>
                              </SelectContent>
                            </Select>
                          </TableCell>
                          <TableCell>
                            <Switch
                              aria-label={`${name} — announce progress`}
                              checked={option.report_progress}
                              onCheckedChange={(v) => patchOption(name, { report_progress: v })}
                            />
                            {option.mode !== "blocking" && !option.report_progress ? (
                              <p className="mt-1 max-w-40 text-[0.6875rem] leading-tight text-warning-text">
                                No progress messages — the agent won&apos;t announce this tool.
                              </p>
                            ) : null}
                          </TableCell>
                          <TableCell>
                            <Select
                              value={option.cancellable}
                              onValueChange={(v) => patchOption(name, { cancellable: v as CancellableDraft })}
                            >
                              <SelectTrigger id={`${rowId}-cancellable`} className="w-28" aria-label={`${name} — can be cancelled`}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="default">Default</SelectItem>
                                <SelectItem value="true">Yes</SelectItem>
                                <SelectItem value="false">No</SelectItem>
                              </SelectContent>
                            </Select>
                          </TableCell>
                          <TableCell>
                            <Select
                              value={option.on_duplicate}
                              onValueChange={(v) => patchOption(name, { on_duplicate: v as DuplicateDraft })}
                            >
                              <SelectTrigger id={`${rowId}-duplicate`} className="w-40" aria-label={`${name} — repeated calls`}>
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
                          </TableCell>
                          {allowedToolNames.length === 0 ? (
                            <TableCell>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => removeRow(name)}
                                aria-label={`Remove ${name}`}
                              >
                                Remove
                              </Button>
                            </TableCell>
                          ) : null}
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-[0.8125rem] text-muted-foreground">
                  No tool names yet — add one below, or list them in &quot;Allowed tools&quot; above.
                </p>
              )}
              {allowedToolNames.length === 0 ? (
                <div className="flex gap-2">
                  <Input
                    value={freeTextRow}
                    onChange={(e) => setFreeTextRow(e.target.value)}
                    placeholder="tool_name"
                    className="font-mono text-sm"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addFreeTextRow();
                      }
                    }}
                  />
                  <Button type="button" variant="outline" onClick={addFreeTextRow} disabled={freeTextRow.trim() === ""}>
                    Add
                  </Button>
                </div>
              ) : null}
            </div>

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
          </DialogBody>

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
