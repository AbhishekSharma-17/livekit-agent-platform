"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { StatusPill } from "@/components/shared/status-chip";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SearchableSelect } from "@/components/ui/searchable-select";
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
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronRightIcon } from "lucide-react";
import { useCreateTool, useTestMcpTool, useUpdateTool } from "@/components/console/lib/api-hooks";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { errorMessage } from "@/components/console/shared/error-banner";
import { AVAILABLE_MCP_PRESETS, MCP_PRESET_AUTH_LABEL, mcpPresetById, type McpPresetAuthMode } from "@/components/console/tools/mcp-presets";
import { McpOauthStatusPanel } from "@/components/console/tools/mcp-oauth-status";
import { BindingsEditor, bindingsHaveIssues } from "@/components/console/tools/bindings-editor";
import { PinnedArgumentsEditor, pinnedArgumentsHaveIssues, type PinnedValue } from "@/components/console/tools/pinned-arguments-editor";
import { ReadbackField, confirmReadbackHasIssues } from "@/components/console/tools/readback-field";
import { RequiresVarsField, requiresVarsHaveIssues } from "@/components/console/tools/requires-vars-field";
import { useAgentToolContextOptions } from "@/components/console/tools/use-agent-tool-context";
import { schemaProperties } from "@/components/console/tools/tool-context";
import type {
  McpHeaderAuth,
  McpNoAuth,
  McpOAuthAuth,
  McpServerDefinition,
  McpTestResult,
  ProviderSpec,
  ToolContextSpec,
  ToolExecution,
  ToolOut,
} from "@/contracts/lkap-contracts";

const EMPTY_TOOL_CONTEXT: ToolContextSpec = { requires_vars: [], confirm_readback: [], bindings: [], pinned_arguments: {} };

type McpAuth = McpNoAuth | McpHeaderAuth | McpOAuthAuth;

/** The four auth modes the dialog offers (`"none"` never appears in a preset's matrix). */
type AuthKind = "none" | McpPresetAuthMode;

const AUTH_KIND_LABEL: Record<AuthKind, string> = {
  none: "No authentication",
  ...MCP_PRESET_AUTH_LABEL,
};

/** Every mode, for a hand-added server with no preset. */
const ALL_AUTH_KINDS: AuthKind[] = ["none", "header", "oauth", "own_oauth"];

interface Draft {
  name: string;
  url: string;
  authKind: AuthKind;
  headersJson: string;
  credential_id: string | null;
  oauthCredentialId: string | null;
  oauthClientId: string;
  oauthScopes: string;
  oauthSubject: "workspace" | "agent";
  allowed_tools: string;
  timeout_s: number;
  sse_read_timeout_s: number;
  enabled: boolean;
}

function authKindOf(def: McpServerDefinition | undefined): AuthKind {
  const auth = def?.auth;
  if (auth?.kind === "header") return "header";
  if (auth?.kind === "oauth") return auth.registration === "preregistered" ? "own_oauth" : "oauth";
  if (auth?.kind === "none") return "none";
  // `auth` is always present once the api's `_fold_legacy_auth` validator has run (it
  // folds `headers`/`credential_id` into `McpHeaderAuth` on every parse) — this is a
  // client-side fallback only, for a definition that somehow arrives with no `auth`.
  if (Object.keys(def?.headers ?? {}).length > 0 || def?.credential_id) return "header";
  return "none";
}

function draftFromTool(tool: ToolOut | undefined): Draft {
  const def = tool?.definition.kind === "mcp" ? tool.definition : undefined;
  const auth = def?.auth;
  const headerAuth = auth?.kind === "header" ? auth : undefined;
  const oauthAuth = auth?.kind === "oauth" ? auth : undefined;
  return {
    name: tool?.name ?? "",
    url: def?.url ?? "",
    authKind: authKindOf(def),
    headersJson: JSON.stringify(headerAuth?.headers ?? def?.headers ?? {}, null, 2),
    credential_id: headerAuth?.credential_id ?? def?.credential_id ?? null,
    oauthCredentialId: oauthAuth?.credential_id ?? null,
    oauthClientId: oauthAuth?.client_id ?? "",
    oauthScopes: (oauthAuth?.scopes ?? []).join(", "),
    oauthSubject: oauthAuth?.subject ?? "workspace",
    allowed_tools: (def?.allowed_tools ?? []).join(", "),
    timeout_s: def?.timeout_s ?? 5,
    sse_read_timeout_s: def?.sse_read_timeout_s ?? 300,
    enabled: tool?.enabled ?? true,
  };
}

/** Only the fields that change what `oauth/start` and `POST .../test` would actually do —
 * `allowed_tools`/`tool_options`/timeouts don't affect the connection itself, so editing
 * only those never blocks Test connection or Sign in. */
function connectionSignature(draft: Draft): string {
  return JSON.stringify({
    url: draft.url,
    authKind: draft.authKind,
    headersJson: draft.authKind === "header" ? draft.headersJson : "",
    credential_id: draft.authKind === "header" ? draft.credential_id : null,
    client_id: draft.authKind === "own_oauth" ? draft.oauthClientId.trim() : "",
    scopes: draft.authKind === "oauth" || draft.authKind === "own_oauth" ? draft.oauthScopes.trim() : "",
    subject: draft.authKind === "oauth" || draft.authKind === "own_oauth" ? draft.oauthSubject : "workspace",
  });
}

interface DraftErrors {
  name?: string;
  url?: string;
  headersJson?: string;
  oauthClientId?: string;
  toolContext?: string;
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
 *
 * V5-21: a preset picker (new servers only), the `auth` mode (None / Header /
 * Sign in with the vendor / Your own OAuth app — docs/v5/_asks.md #66: reads
 * and posts `auth`, never the deprecated `headers`/`credential_id` mirrors,
 * and never echoes `cached_tools` back), the sign-in panel
 * (`mcp-oauth-status.tsx`) and "Test connection" (`POST .../test`) with
 * checkboxes that build `allowed_tools` from the server's own tool list.
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
  const [presetId, setPresetId] = React.useState<string | null>(null);
  const createTool = useCreateTool();
  const updateTool = useUpdateTool();
  const testMcp = useTestMcpTool();

  const initialSignature = React.useRef(connectionSignature(draftFromTool(tool)));
  const [testResult, setTestResult] = React.useState<McpTestResult | null>(null);
  const def = tool?.definition.kind === "mcp" ? tool.definition : undefined;
  const [discovered, setDiscovered] = React.useState<string[]>(() => (def?.cached_tools ?? []).map((t) => t.name));

  const existingOptions = tool?.definition.kind === "mcp" ? (tool.definition.tool_options ?? {}) : {};
  const [toolOptions, setToolOptions] = React.useState<Record<string, McpOptionDraft>>(() =>
    Object.fromEntries(Object.entries(existingOptions).map(([name, exec]) => [name, mcpOptionFromExecution(exec)])),
  );
  const [freeTextRow, setFreeTextRow] = React.useState("");
  // V6-11 (D-V6-22, ask #40): `requires_vars`/`confirm_readback`/`bindings`/`pinned_arguments`
  // per allowed tool — same "only rows the admin touched" convention as `toolOptions`.
  const [toolContext, setToolContext] = React.useState<Record<string, ToolContextSpec>>(() => def?.tool_context ?? {});
  const [openContextRow, setOpenContextRow] = React.useState<string | null>(null);
  const agentContext = useAgentToolContextOptions(agentId);
  const cachedSchemas = React.useMemo(
    () => new Map((def?.cached_tools ?? []).map((snapshot) => [snapshot.name, schemaProperties(snapshot.input_schema)])),
    [def?.cached_tools],
  );

  function contextFor(name: string): ToolContextSpec {
    return toolContext[name] ?? EMPTY_TOOL_CONTEXT;
  }

  function patchContext(name: string, patch: Partial<ToolContextSpec>) {
    setToolContext((current) => ({ ...current, [name]: { ...contextFor(name), ...patch } }));
  }

  // Full reset, but only on the transition to `open` — never on a `tool` identity change
  // while the dialog is already open. Test connection's own success handler invalidates
  // the `tools` list (it just wrote `cached_tools` server-side), and Connect/Disconnect do
  // the same for the credential id below; if this effect also depended on `tool`, that
  // refetch would wipe the "OK · n tools" result and any checkbox the admin had already
  // toggled a moment after they appeared.
  React.useEffect(() => {
    if (!open) return;
    const fresh = draftFromTool(tool);
    setDraft(fresh);
    initialSignature.current = connectionSignature(fresh);
    const nextDef = tool?.definition.kind === "mcp" ? tool.definition : undefined;
    const options = nextDef?.tool_options ?? {};
    setToolOptions(
      Object.fromEntries(Object.entries(options).map(([name, exec]) => [name, mcpOptionFromExecution(exec)])),
    );
    setDiscovered((nextDef?.cached_tools ?? []).map((t) => t.name));
    setFreeTextRow("");
    setToolContext(nextDef?.tool_context ?? {});
    setOpenContextRow(null);
    setErrors({});
    setPresetId(null);
    setTestResult(null);
    // `tool` is read once per open, deliberately excluded from the deps (see above).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // While already open, a background refetch of the same tool (Test connection, Connect,
  // Disconnect all invalidate the `tools` list) may hand us a new `tool` object. Sync only
  // the server-owned fields a refetch is actually meant to update — never the draft, the
  // execution options, or the just-shown test result the admin may be mid-edit of.
  React.useEffect(() => {
    if (!open || !tool || tool.definition.kind !== "mcp") return;
    const def = tool.definition;
    const credentialId = def.auth?.kind === "oauth" ? (def.auth.credential_id ?? null) : null;
    setDraft((d) => (d.oauthCredentialId === credentialId ? d : { ...d, oauthCredentialId: credentialId }));
    const cachedNames = (def.cached_tools ?? []).map((t) => t.name);
    if (cachedNames.length > 0) {
      // Never shrink back to a stale, earlier fetch's shorter list — only adopt a server
      // list that is at least as complete as what a just-run Test connection already put here.
      setDiscovered((current) => (cachedNames.length >= current.length ? cachedNames : current));
    }
  }, [open, tool]);

  const dirty = tool ? connectionSignature(draft) !== initialSignature.current : true;

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

  function applyPreset(id: string) {
    const preset = mcpPresetById(id);
    if (!preset) return;
    setPresetId(id);
    setDraft((d) => ({
      ...d,
      name: d.name.trim() === "" ? preset.name : d.name,
      url: preset.url,
      authKind: preset.auth_matrix[0] ?? "header",
    }));
  }

  const preset = presetId ? mcpPresetById(presetId) : undefined;
  const availableAuthKinds: AuthKind[] = preset ? preset.auth_matrix : ALL_AUTH_KINDS;

  function toggleDiscoveredTool(name: string, checked: boolean) {
    setDraft((d) => {
      const current = allowedToolNames.length > 0 ? allowedToolNames : discovered;
      const next = checked ? Array.from(new Set([...current, name])) : current.filter((n) => n !== name);
      // An empty `allowed_tools` means "no restriction" to the contract, the opposite of
      // what unchecking the last box means here — refuse rather than silently re-allowing
      // every tool the admin just excluded.
      if (next.length === 0) return d;
      return { ...d, allowed_tools: next.join(", ") };
    });
  }

  async function handleTest() {
    if (!tool) return;
    setTestResult(null);
    try {
      const result = await testMcp.mutateAsync(tool.id);
      setTestResult(result);
      if (result.ok) {
        const names = result.tool_names ?? [];
        setDiscovered(names);
        if (allowedToolNames.length === 0 && preset && preset.allowed_tools_default.length > 0) {
          const defaults = preset.allowed_tools_default.filter((name) => names.includes(name));
          if (defaults.length > 0) setDraft((d) => ({ ...d, allowed_tools: defaults.join(", ") }));
        }
      }
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();

    const nextErrors: DraftErrors = {};

    let headers: Record<string, string> = {};
    if (draft.authKind === "header") {
      try {
        headers = draft.headersJson.trim() === "" ? {} : (JSON.parse(draft.headersJson) as Record<string, string>);
      } catch {
        nextErrors.headersJson = "Must be valid JSON.";
      }
    }
    if (draft.name.trim() === "") nextErrors.name = "Name is required.";
    if (draft.url.trim() === "") nextErrors.url = "URL is required.";
    // No save-time check on the client id: `McpOAuthAuth.client_id` is optional, and an
    // admin choosing "Your own OAuth app" needs to save the server *before* they can see
    // this deployment's return address (it only appears once Sign in is attempted) — the
    // client id comes after that, on a second save. `clientIdSet` below is what actually
    // gates Sign in.

    const allowedTools = draft.allowed_tools
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    // A stale free-text row from before `allowed_tools` was narrowed would
    // otherwise still post an override for a name the server rejects
    // ("not one of this server's allowed_tools").
    const validOptionNames = allowedTools.length > 0 ? new Set(allowedTools) : null;

    // V6-11 (D-V6-22): each tool's own requires_vars/confirm_readback/bindings/pinned_arguments.
    const contextEntries = Object.entries(toolContext).filter(
      ([name]) => !validOptionNames || validOptionNames.has(name),
    );
    const contextHasIssues = contextEntries.some(([name, spec]) => {
      const pinnedNames = Object.keys(spec.pinned_arguments ?? {});
      const properties = cachedSchemas.get(name);
      return (
        requiresVarsHaveIssues(spec.requires_vars ?? []) ||
        confirmReadbackHasIssues(spec.confirm_readback ?? [], properties ? Array.from(properties) : null, pinnedNames) ||
        bindingsHaveIssues(spec.bindings ?? []) ||
        pinnedArgumentsHaveIssues(spec.pinned_arguments ?? {})
      );
    });
    if (contextHasIssues) nextErrors.toolContext = "Fix the tool context below (open each tool's settings to see what's wrong).";

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    let auth: McpAuth;
    switch (draft.authKind) {
      case "none":
        auth = { kind: "none" };
        break;
      case "header":
        auth = { kind: "header", headers, credential_id: draft.credential_id };
        break;
      case "oauth":
      case "own_oauth": {
        const scopes = draft.oauthScopes
          .split(/[,\s]+/)
          .map((s) => s.trim())
          .filter(Boolean);
        auth = {
          kind: "oauth",
          registration: draft.authKind === "own_oauth" ? "preregistered" : "auto",
          client_id: draft.authKind === "own_oauth" ? draft.oauthClientId.trim() : null,
          credential_id: draft.oauthCredentialId,
          scopes: scopes.length > 0 ? scopes : null,
          subject: draft.oauthSubject,
        };
        break;
      }
    }

    // docs/v5/_asks.md #66: `auth` only — the deprecated top-level mirrors are left at
    // their defaults ({}/`null`) so the contract's own validator fills them from `auth`
    // (an oauth server errors if `headers` is non-empty at the top level). `cached_tools`
    // /`cached_at` are the api's own record of the last test and are never sent back.
    const definition: McpServerDefinition = {
      kind: "mcp",
      name: draft.name,
      url: draft.url,
      auth,
      allowed_tools: allowedTools.length > 0 ? allowedTools : null,
      timeout_s: draft.timeout_s,
      sse_read_timeout_s: draft.sse_read_timeout_s,
      // Only rows the admin touched (D-V4-32: an unedited row means "no override").
      tool_options: Object.fromEntries(
        Object.entries(toolOptions)
          .filter(([name]) => !validOptionNames || validOptionNames.has(name))
          .map(([name, option]) => [name, executionFromMcpOption(option)]),
      ),
      ...(contextEntries.length > 0 ? { tool_context: Object.fromEntries(contextEntries) } : {}),
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
  const isOauth = draft.authKind === "oauth" || draft.authKind === "own_oauth";
  const testDisabledReason = !tool ? "Save the server first, then test the connection." : dirty ? "Save your changes first." : undefined;

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
            {!tool ? (
              <Field
                label="Start from a preset"
                htmlFor={`${uid}-preset`}
                optional
                hint="Fills in a known server's address and auth options — edit anything below before saving."
              >
                <SearchableSelect
                  id={`${uid}-preset`}
                  value={presetId}
                  onValueChange={applyPreset}
                  options={AVAILABLE_MCP_PRESETS.map((p) => ({ value: p.id, label: p.name }))}
                  placeholder="Choose a preset (optional)"
                  searchPlaceholder="Search presets"
                  aria-label="Start from a preset"
                />
              </Field>
            ) : null}
            {preset?.notes ? <p className="text-label text-pretty text-text-secondary">{preset.notes}</p> : null}

            <Field label="Name" htmlFor={`${uid}-name`} required error={errors.name}>
              <Input id={`${uid}-name`} value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} />
            </Field>
            <Field label="URL" htmlFor={`${uid}-url`} required error={errors.url}>
              <Input
                id={`${uid}-url`}
                className="font-mono text-body"
                value={draft.url}
                onChange={(e) => setDraft((d) => ({ ...d, url: e.target.value }))}
                placeholder="https://mcp.example.com/stream"
              />
            </Field>

            <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
              <legend className="mb-1 text-body font-medium text-foreground">Authentication</legend>
              <RadioGroup
                value={draft.authKind}
                onValueChange={(value) => setDraft((d) => ({ ...d, authKind: value as AuthKind }))}
              >
                {availableAuthKinds.map((kind) => (
                  <Label
                    key={kind}
                    htmlFor={`${uid}-auth-${kind}`}
                    className={`flex cursor-pointer items-center gap-2 rounded border p-2.5 text-body font-normal ${
                      draft.authKind === kind ? "border-brand bg-muted/50" : "border-border"
                    }`}
                  >
                    <RadioGroupItem id={`${uid}-auth-${kind}`} value={kind} />
                    {AUTH_KIND_LABEL[kind]}
                  </Label>
                ))}
              </RadioGroup>
              {preset?.scopes_hint ? <p className="text-caption text-text-secondary">{preset.scopes_hint}</p> : null}
            </fieldset>

            {draft.authKind === "header" ? (
              <>
                <Field
                  label="Headers"
                  htmlFor={`${uid}-headers`}
                  hint={"JSON; values may use {{ secret.NAME }}."}
                  error={errors.headersJson}
                >
                  <Textarea
                    id={`${uid}-headers`}
                    className="min-h-20 font-mono text-caption"
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
              </>
            ) : null}

            {isOauth ? (
              <>
                {draft.authKind === "own_oauth" ? (
                  <>
                    <p className="rounded bg-muted/50 px-3 py-2 text-label text-text-secondary">
                      Register an app with the vendor first — its return address is the api&apos;s own address
                      (not this console&apos;s) with <code className="font-mono">/v1/oauth/mcp/callback</code>{" "}
                      appended. Once you have a client id, save this server, then use Sign in below.
                    </p>
                    <Field
                      label="Client id"
                      htmlFor={`${uid}-oauth-client-id`}
                      optional
                      error={errors.oauthClientId}
                      hint="From the OAuth app you register with the vendor. Sign in stays disabled until this is set and saved."
                    >
                      <Input
                        id={`${uid}-oauth-client-id`}
                        value={draft.oauthClientId}
                        onChange={(e) => setDraft((d) => ({ ...d, oauthClientId: e.target.value }))}
                      />
                    </Field>
                  </>
                ) : null}
                <Field
                  label="Scope override"
                  htmlFor={`${uid}-oauth-scopes`}
                  optional
                  hint="Space or comma separated. Leave blank to use the server's suggested scopes."
                >
                  <Input
                    id={`${uid}-oauth-scopes`}
                    value={draft.oauthScopes}
                    onChange={(e) => setDraft((d) => ({ ...d, oauthScopes: e.target.value }))}
                  />
                </Field>
                {agentId !== null ? (
                  <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
                    <legend className="mb-1 text-body font-medium text-foreground">Sign in as</legend>
                    <RadioGroup
                      value={draft.oauthSubject}
                      onValueChange={(value) => setDraft((d) => ({ ...d, oauthSubject: value as Draft["oauthSubject"] }))}
                      className="flex flex-col gap-2"
                    >
                      <Label htmlFor={`${uid}-subject-workspace`} className="flex cursor-pointer items-center gap-2 text-body font-normal">
                        <RadioGroupItem id={`${uid}-subject-workspace`} value="workspace" />
                        This workspace
                      </Label>
                      <Label htmlFor={`${uid}-subject-agent`} className="flex cursor-pointer items-center gap-2 text-body font-normal">
                        <RadioGroupItem id={`${uid}-subject-agent`} value="agent" />
                        This agent
                      </Label>
                    </RadioGroup>
                  </fieldset>
                ) : null}
                <McpOauthStatusPanel
                  toolId={tool?.id ?? null}
                  registration={draft.authKind === "own_oauth" ? "own_oauth" : "oauth"}
                  clientIdSet={draft.authKind !== "own_oauth" || draft.oauthClientId.trim() !== ""}
                  dirty={dirty}
                />
              </>
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
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-caption font-semibold tracking-wide text-text-secondary">Test connection</h3>
                <Button type="button" variant="secondary" size="sm" onClick={() => void handleTest()} disabled={Boolean(testDisabledReason) || testMcp.isPending} title={testDisabledReason}>
                  {testMcp.isPending ? "Testing…" : "Test connection"}
                </Button>
              </div>
              {testDisabledReason ? <p className="text-label text-text-secondary">{testDisabledReason}</p> : null}
              {testResult ? (
                <div className="flex items-center gap-2 text-label">
                  <StatusPill tone={testResult.ok ? "success" : "danger"} size="sm">
                    {testResult.ok ? "OK" : "Failed"}
                  </StatusPill>
                  <span className="text-text-secondary">
                    {testResult.ok ? `${testResult.tool_count} tools · ${testResult.duration_ms}ms` : (testResult.error ?? "Couldn't connect.")}
                  </span>
                </div>
              ) : null}
              {discovered.length > 0 ? (
                <div className="flex flex-col gap-1.5">
                  <p className="text-label text-text-secondary">Uncheck a tool to keep it out of &quot;Allowed tools&quot;.</p>
                  <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                    {discovered.map((name) => {
                      const checked = allowedToolNames.length === 0 ? true : allowedToolNames.includes(name);
                      return (
                        <label key={name} className="flex items-center gap-1.5 text-caption">
                          <input
                            type="checkbox"
                            className="size-3.5 rounded-sm border-input"
                            checked={checked}
                            onChange={(e) => toggleDiscoveredTool(name, e.target.checked)}
                          />
                          <span className="font-mono">{name}</span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="flex flex-col gap-2 border-t border-border pt-4">
              <h3 className="text-caption font-semibold tracking-wide text-text-secondary">How each tool runs</h3>
              <p className="text-label text-text-secondary">
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
                          <TableCell className="font-mono text-caption">{name}</TableCell>
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
                              <p className="mt-1 max-w-40 text-caption leading-tight text-warning-text">
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
                <p className="text-label text-text-secondary">
                  No tool names yet — add one below, or list them in &quot;Allowed tools&quot; above.
                </p>
              )}
              {allowedToolNames.length === 0 ? (
                <div className="flex gap-2">
                  <Input
                    value={freeTextRow}
                    onChange={(e) => setFreeTextRow(e.target.value)}
                    placeholder="tool_name"
                    aria-label="Tool name"
                    className="font-mono text-body"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addFreeTextRow();
                      }
                    }}
                  />
                  <Button type="button" variant="secondary" onClick={addFreeTextRow} disabled={freeTextRow.trim() === ""}>
                    Add
                  </Button>
                </div>
              ) : null}
            </div>

            {optionRows.length > 0 ? (
              <div className="flex flex-col gap-2 border-t border-border pt-4">
                <h3 className="text-caption font-semibold tracking-wide text-text-secondary">Tool context</h3>
                <p className="text-label text-text-secondary">
                  Per tool: session values it can use, what it needs first, what it reads back, where a result goes,
                  and any fixed values.
                </p>
                {errors.toolContext ? <p className="text-label text-destructive-text">{errors.toolContext}</p> : null}
                {!agentId ? (
                  <p className="text-label text-text-secondary">
                    Attach this server to an agent to pick from its panel blocks and flow variables.
                  </p>
                ) : null}
                <div className="flex flex-col gap-1.5">
                  {optionRows.map((name) => {
                    const spec = contextFor(name);
                    const properties = cachedSchemas.get(name);
                    const pinnedNames = Object.keys(spec.pinned_arguments ?? {});
                    return (
                      <Collapsible
                        key={name}
                        open={openContextRow === name}
                        onOpenChange={(next) => setOpenContextRow(next ? name : null)}
                      >
                        <CollapsibleTrigger asChild>
                          <Button type="button" variant="secondary" size="sm" className="w-fit justify-start gap-1.5 text-caption">
                            <ChevronRightIcon
                              className={`size-3.5 shrink-0 transition-transform ${openContextRow === name ? "rotate-90" : ""}`}
                              aria-hidden="true"
                            />
                            {`Tool context — ${name}`}
                          </Button>
                        </CollapsibleTrigger>
                        <CollapsibleContent className="flex flex-col gap-5 rounded border border-border p-3 pt-4">
                          <RequiresVarsField
                            uid={`${uid}-${name}`}
                            values={spec.requires_vars ?? []}
                            onChange={(requires_vars) => patchContext(name, { requires_vars })}
                            knownVariables={agentContext.variableNames}
                          />
                          <ReadbackField
                            uid={`${uid}-${name}`}
                            values={spec.confirm_readback ?? []}
                            onChange={(confirm_readback) => patchContext(name, { confirm_readback })}
                            argumentNames={properties ? Array.from(properties) : null}
                            pinnedNames={pinnedNames}
                          />
                          <BindingsEditor
                            uid={`${uid}-${name}`}
                            values={spec.bindings ?? []}
                            onChange={(bindings) => patchContext(name, { bindings })}
                            detailsBlocks={agentContext.detailsBlocks}
                            tableBlocks={agentContext.tableBlocks}
                          />
                          <PinnedArgumentsEditor
                            values={(spec.pinned_arguments ?? {}) as Record<string, PinnedValue>}
                            onChange={(pinned_arguments) => patchContext(name, { pinned_arguments })}
                            variableNames={agentContext.variableNames}
                          />
                        </CollapsibleContent>
                      </Collapsible>
                    );
                  })}
                </div>
              </div>
            ) : null}

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
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
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
