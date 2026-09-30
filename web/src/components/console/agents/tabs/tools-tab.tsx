"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";
import { ChevronRightIcon, PlusIcon, XIcon } from "lucide-react";

import { Field, fieldIds } from "@/components/shared/field";
import { Section, SectionRow } from "@/components/shared/section";
import { Icon } from "@/components/shared/icon";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePacks, useProviders, useTools } from "@/components/console/lib/api-hooks";
import { BUILTIN_TOOLS, type BuiltinToolInfo } from "@/components/console/lib/constants";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { BuiltinExecutionDialog } from "@/components/console/agents/tabs/builtin-execution-dialog";
import { ConnectedAppsCard } from "@/components/console/agents/tabs/connected-apps-card";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import { displayMessage } from "@/components/console/agents/editor/validation-map";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import { ProviderSlotCard } from "@/components/console/registry/provider-slot-card";
import { DatasetToolEditorDialog } from "@/components/console/tools/dataset-tool-editor-dialog";
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { KitGallery } from "@/components/console/tools/kits/kit-gallery";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { ToolRow } from "@/components/console/tools/tool-row";
import { ToolTemplateDialog } from "@/components/console/tools/tool-template-dialog";
import { originOf } from "@/components/console/tools/tools-list";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { cn } from "@/lib/utils";
import type { AgentOut, NotifyTeamConfig, ProviderSpec, ToolExecution, ToolOut } from "@/contracts/lkap-contracts";

/**
 * Built-in tool groups (§4.7): the tool names come from WP-0's
 * `BUILTIN_TOOLS` (`constants.ts`, not owned by this package), grouped here
 * since the contract carries no grouping of its own.
 */
const BUILTIN_GROUPS: { label: string; tools: string[] }[] = [
  { label: "Conversation", tools: ["end_call", "current_time", "convert_time"] },
  // V5-25/V5-28: plain built-ins with no service of their own — a switch is all they need.
  { label: "Utilities", tools: ["calculate", "spell_back"] },
  { label: "Knowledge", tools: ["search_knowledge"] },
  { label: "Vision", tools: ["describe_current_frame", "pin_frame"] },
  { label: "Panel", tools: ["push_note", "set_status"] },
  { label: "Escalation", tools: ["escalate_to_human"] },
];

/** `BACKGROUNDABLE_BUILTINS` (BACKGROUND-TOOLS.md §3) — everything else always blocks. */
const BUILTINS_WITH_EXECUTION = new Set(["search_knowledge", "http_request", "describe_current_frame"]);

/** Below this, a chain of background announcements can exhaust the tool-steps budget (api's `MIN_TOOL_STEPS_FOR_BACKGROUND`, R-V4-35). */
const MIN_TOOL_STEPS_FOR_BACKGROUND = 4;

export function ToolsTab({ agent }: { agent: AgentOut }) {
  const maxToolStepsId = React.useId();
  const { control, watch, setValue } = useFormContext<AgentEditorForm>();
  const toolIds = watch("config.tools.tool_ids");
  const builtinDisabled = watch("config.tools.builtin_disabled");
  const builtinExecution = watch("config.tools.builtin_execution");
  const executionDefault = watch("config.tools.execution_default");
  const maxToolSteps = watch("config.tools.max_tool_steps");
  const kbIds = watch("config.knowledge.kb_ids");
  const camera = watch("config.capabilities.camera");
  const screenShare = watch("config.capabilities.screen_share");
  // Instructions & voice's Conversation card shows the same warning; a
  // server-issued `tools.max_tool_steps` issue routes to this section
  // (`builtin-sections.tsx`), so this is the field `focusFieldFor` actually
  // needs to reach — auto-open Advanced so it is visible and focusable.
  const stepsWarning = executionDefault !== "blocking" && maxToolSteps < MIN_TOOL_STEPS_FOR_BACKGROUND;
  const [advancedOpen, setAdvancedOpen] = React.useState(stepsWarning);
  React.useEffect(() => {
    if (stepsWarning) setAdvancedOpen(true);
  }, [stepsWarning]);

  const ownToolsQuery = useTools(agent.id);
  const allToolsQuery = useTools();
  const providersQuery = useProviders();
  const packsQuery = usePacks();
  // V5-25/V5-28: the network built-ins' provider slots and validation issues.
  const webSearch = watch("config.tools.web_search");
  const sms = watch("config.tools.sms");
  const fetchUrlHosts = watch("config.tools.fetch_url_allowed_hosts") ?? [];
  const notifyTeam = watch("config.tools.notify_team");
  const { issueFor } = useSectionIssues("tools");
  const [expandedSlot, setExpandedSlot] = React.useState<"web_search" | "sms" | null>(null);

  const secretBagSpec = providersQuery.data?.providers.find((p) => p.kind === "secret_bag");
  const pack = packsQuery.data?.items.find((p) => p.manifest.id === agent.pack_id)?.manifest;

  function slotProblem(path: string): { message: string; tone: "error" | "warning" } | undefined {
    const issue = issueFor(path);
    if (!issue) return undefined;
    return { message: displayMessage(issue), tone: issue.severity === "error" ? "error" : "warning" };
  }

  function setAttached(id: string, attached: boolean) {
    const current = toolIds ?? [];
    setValue(
      "config.tools.tool_ids",
      attached ? Array.from(new Set([...current, id])) : current.filter((existing) => existing !== id),
      { shouldDirty: true },
    );
  }

  function toggleBuiltin(name: string, enabled: boolean) {
    const current = builtinDisabled ?? [];
    setValue(
      "config.tools.builtin_disabled",
      enabled ? current.filter((existing) => existing !== name) : Array.from(new Set([...current, name])),
      { shouldDirty: true },
    );
  }

  /** Writes `config.tools.builtin_execution[name]` (BACKGROUND-TOOLS.md §7). */
  function setBuiltinExecution(name: string, execution: ToolExecution) {
    setValue("config.tools.builtin_execution", { ...(builtinExecution ?? {}), [name]: execution }, { shouldDirty: true });
  }

  function executionChipFor(name: string, label: string) {
    if (!BUILTINS_WITH_EXECUTION.has(name)) return null;
    return (
      <BuiltinExecutionDialog
        name={name}
        label={label}
        value={builtinExecution?.[name]}
        onSave={(execution) => setBuiltinExecution(name, execution)}
        trigger={
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            aria-label={`Execution — ${label}`}
          >
            Execution
          </Button>
        }
      />
    );
  }

  const byName = new Map(BUILTIN_TOOLS.map((t) => [t.name, t]));
  const hasKnowledge = (kbIds ?? []).length > 0;
  const hasVision = Boolean(camera || screenShare);

  const ownTools = ownToolsQuery.data?.items ?? [];
  const httpTools = ownTools.filter((t) => t.kind === "http");
  const datasetTools = ownTools.filter((t) => t.kind === "dataset");
  // A Composio app server / tool finder is created with this exact
  // `agent_id` (`tool_providers/provisioning.py`), so it would otherwise
  // show here too, editable — it's managed from the Connected apps card
  // above instead (`originOf`, docs/v5/COMPOSIO.md §6).
  const mcpTools = ownTools.filter((t) => t.kind === "mcp" && !originOf(t));
  const sharedAttachable = (allToolsQuery.data?.items ?? []).filter(
    (t) => t.agent_id === null && !(toolIds ?? []).includes(t.id),
  );
  const [sharedToAttach, setSharedToAttach] = React.useState("");

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="tools-builtin"
        title="Built-in tools"
        description="Available to every agent; turn off the ones this agent shouldn't use."
      >
        {BUILTIN_GROUPS.map((group) => {
          const disabledReason =
            group.label === "Knowledge" && !hasKnowledge
              ? "Attach a knowledge base first"
              : group.label === "Vision" && !hasVision
                ? "Turn on camera or screen share first"
                : null;
          return (
            <React.Fragment key={group.label}>
              <SectionRow compact className="bg-muted/40">
                <p className="text-xs font-medium text-text-secondary">{group.label}</p>
              </SectionRow>
              {group.tools.map((name) => {
                const tool = byName.get(name);
                if (!tool) return null;
                return (
                  <BuiltinToolRow
                    key={name}
                    tool={tool}
                    checked={!(builtinDisabled ?? []).includes(name)}
                    disabledReason={disabledReason}
                    onCheckedChange={(checked) => toggleBuiltin(name, checked)}
                    executionChip={executionChipFor(name, tool.label)}
                  />
                );
              })}
            </React.Fragment>
          );
        })}

        <SectionRow compact className="bg-muted/40">
          <p className="text-xs font-medium text-text-secondary">Team notifications</p>
        </SectionRow>
        <SectionRow>
          <NotifyTeamCard
            value={notifyTeam}
            onChange={(next) => setValue("config.tools.notify_team", next, { shouldDirty: true })}
            secretBagSpec={secretBagSpec}
            error={slotProblem("tools.notify_team.credential_id") ?? slotProblem("tools.notify_team")}
          />
        </SectionRow>

        <SectionRow compact className="bg-muted/40">
          <p className="text-xs font-medium text-text-secondary">Network</p>
        </SectionRow>
        <SectionRow>
          <Field
            inline
            label="Make HTTP requests"
            htmlFor="http-request-enabled"
            hint="Lets the model call web addresses on its own, limited to the hosts the worker allows (LKAP_HTTP_TOOL_ALLOWED_HOSTS)."
          >
            <div className="flex items-center gap-2">
              {executionChipFor("http_request", "Make HTTP requests")}
              <Controller
                control={control}
                name="config.tools.http_request_enabled"
                render={({ field }) => (
                  <Switch
                    id="http-request-enabled"
                    checked={field.value}
                    onCheckedChange={field.onChange}
                    aria-describedby={fieldIds("http-request-enabled").hint}
                  />
                )}
              />
            </div>
          </Field>
        </SectionRow>

        <SectionRow>
          <ProviderSlotCard
            title={byName.get("web_search")?.label ?? "Search the web"}
            description={byName.get("web_search")?.help}
            kind="web_search"
            value={webSearch ?? null}
            onChange={(next) => setValue("config.tools.web_search", next, { shouldDirty: true })}
            constraints={{ required: false, inference: "off" }}
            idPrefix="slot-web-search"
            issuePath="tools.web_search"
            expanded={expandedSlot === "web_search"}
            onExpandedChange={(open) => setExpandedSlot(open ? "web_search" : null)}
            onRemove={webSearch ? () => setValue("config.tools.web_search", null, { shouldDirty: true }) : undefined}
            error={slotProblem("tools.web_search")?.message}
            errorTone={slotProblem("tools.web_search")?.tone}
          />
        </SectionRow>

        <SectionRow>
          <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
            <div className="flex flex-col gap-0.5">
              <h3 className="text-sm font-semibold text-foreground">{byName.get("fetch_url")?.label ?? "Read a web page"}</h3>
              <p className="text-xs text-pretty text-text-secondary">{byName.get("fetch_url")?.help}</p>
            </div>
            <AllowedHostsEditor
              value={fetchUrlHosts}
              onChange={(next) => setValue("config.tools.fetch_url_allowed_hosts", next, { shouldDirty: true })}
              error={slotProblem("tools.fetch_url_allowed_hosts")?.message}
            />
          </div>
        </SectionRow>

        <SectionRow>
          <ProviderSlotCard
            title={byName.get("send_sms")?.label ?? "Send a text message"}
            description={byName.get("send_sms")?.help}
            kind="sms"
            value={sms ?? null}
            onChange={(next) => setValue("config.tools.sms", next, { shouldDirty: true })}
            constraints={{ required: false, inference: "off" }}
            idPrefix="slot-sms"
            issuePath="tools.sms"
            expanded={expandedSlot === "sms"}
            onExpandedChange={(open) => setExpandedSlot(open ? "sms" : null)}
            onRemove={sms ? () => setValue("config.tools.sms", null, { shouldDirty: true }) : undefined}
            error={slotProblem("tools.sms")?.message}
            errorTone={slotProblem("tools.sms")?.tone}
            notice={
              <p className="text-label text-text-secondary">
                The saved numbers it may text besides the caller live in the Telephony section, below.
              </p>
            }
          />
        </SectionRow>

        <SectionRow>
          <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
            <CollapsibleTrigger
              className={cn(
                "group/more inline-flex items-center gap-1 rounded-sm text-label font-medium text-text-secondary outline-none",
                "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <ChevronRightIcon
                className="size-3.5 transition-transform duration-(--duration-base) group-data-[state=open]/more:rotate-90"
                aria-hidden="true"
              />
              Advanced
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-3">
              <Field
                label="Tool steps per turn"
                htmlFor={maxToolStepsId}
                hint={
                  stepsWarning
                    ? `Read tools run "${executionDefault}" and each announcement spends a step; use ${MIN_TOOL_STEPS_FOR_BACKGROUND} or more.`
                    : "How many tool calls the model may chain before it must reply."
                }
              >
                <Controller
                  control={control}
                  name="config.tools.max_tool_steps"
                  render={({ field }) => (
                    <Input
                      id={maxToolStepsId}
                      type="number"
                      inputMode="numeric"
                      className="w-32"
                      value={field.value}
                      onChange={(event) => field.onChange(Number(event.target.value))}
                      data-issue-path="tools.max_tool_steps"
                    />
                  )}
                />
              </Field>
            </CollapsibleContent>
          </Collapsible>
        </SectionRow>
      </Section>

      <ConnectedAppsCard agentId={agent.id} />

      <KitGallery agent={agent} />

      <Section
        id="tools-dataset"
        title="Lookup tables"
        description="Look a caller up in one of the workspace's lookup tables."
        aside={
          <DatasetToolEditorDialog
            agentId={agent.id}
            onSaved={(tool) => {
              setAttached(tool.id, true);
              void ownToolsQuery.refetch();
            }}
            trigger={
              <Button type="button" variant="outline" size="sm">
                <PlusIcon className="size-3.5" /> Add lookup tool
              </Button>
            }
          />
        }
      >
        <SectionRow>
          {ownToolsQuery.isError ? (
            <ErrorBanner message={errorMessage(ownToolsQuery.error)} onRetry={() => ownToolsQuery.refetch()} />
          ) : ownToolsQuery.isLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : datasetTools.length === 0 ? (
            <EmptyState compact title="No lookup tools yet" description="Look a record up in a workspace lookup table." />
          ) : (
            <div className="space-y-2">
              {datasetTools.map((tool) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  agentId={agent.id}
                  attached={(toolIds ?? []).includes(tool.id)}
                  onToggleAttach={(attached) => setAttached(tool.id, attached)}
                  onSaved={() => void ownToolsQuery.refetch()}
                  onDeleted={() => {
                    setAttached(tool.id, false);
                    void ownToolsQuery.refetch();
                  }}
                  secretBagSpec={secretBagSpec}
                />
              ))}
            </div>
          )}
          {datasetTools.length > 0 ? (
            <p className="mt-2 text-label text-text-secondary">Saved automatically to this agent.</p>
          ) : null}
        </SectionRow>
      </Section>

      <Section
        id="tools-http"
        title="HTTP tools"
        description="Custom calls to an external API, exposed to the model as a function."
        aside={
          <div className="flex flex-wrap gap-2">
            <ToolTemplateDialog
              agentId={agent.id}
              secretBagSpec={secretBagSpec}
              businessTimezone={agent.config.timezone}
              onInstantiated={(result) => {
                for (const id of result.tool_ids) setAttached(id, true);
                void ownToolsQuery.refetch();
              }}
              trigger={
                <Button type="button" variant="outline" size="sm">
                  <PlusIcon className="size-3.5" /> From a template
                </Button>
              }
            />
            <HttpToolEditorDialog
              agentId={agent.id}
              secretBagSpec={secretBagSpec}
              onSaved={(tool) => {
                setAttached(tool.id, true);
                void ownToolsQuery.refetch();
              }}
              trigger={
                <Button type="button" variant="outline" size="sm">
                  <PlusIcon className="size-3.5" /> Add HTTP tool
                </Button>
              }
            />
          </div>
        }
      >
        <SectionRow>
          {ownToolsQuery.isError ? (
            <ErrorBanner message={errorMessage(ownToolsQuery.error)} onRetry={() => ownToolsQuery.refetch()} />
          ) : ownToolsQuery.isLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : httpTools.length === 0 ? (
            <EmptyState compact title="No HTTP tools yet" description="Connect an API the agent can call." />
          ) : (
            <div className="space-y-2">
              {httpTools.map((tool) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  agentId={agent.id}
                  attached={(toolIds ?? []).includes(tool.id)}
                  onToggleAttach={(attached) => setAttached(tool.id, attached)}
                  onSaved={() => void ownToolsQuery.refetch()}
                  onDeleted={() => {
                    setAttached(tool.id, false);
                    void ownToolsQuery.refetch();
                  }}
                  secretBagSpec={secretBagSpec}
                />
              ))}
            </div>
          )}
          {httpTools.length > 0 ? (
            <p className="mt-2 text-label text-text-secondary">Saved automatically to this agent.</p>
          ) : null}
        </SectionRow>
      </Section>

      <Section
        id="tools-mcp"
        title="MCP servers"
        description="A remote MCP server whose tools become available to the model."
        aside={
          <McpToolEditorDialog
            agentId={agent.id}
            secretBagSpec={secretBagSpec}
            onSaved={(tool) => {
              setAttached(tool.id, true);
              void ownToolsQuery.refetch();
            }}
            trigger={
              <Button type="button" variant="outline" size="sm">
                <PlusIcon className="size-3.5" /> Add MCP server
              </Button>
            }
          />
        }
      >
        <SectionRow>
          {mcpTools.length === 0 ? (
            <EmptyState compact title="No MCP servers yet" description="Connect an MCP server the agent can use." />
          ) : (
            <div className="space-y-2">
              {mcpTools.map((tool: ToolOut) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  agentId={agent.id}
                  attached={(toolIds ?? []).includes(tool.id)}
                  onToggleAttach={(attached) => setAttached(tool.id, attached)}
                  onSaved={() => void ownToolsQuery.refetch()}
                  onDeleted={() => {
                    setAttached(tool.id, false);
                    void ownToolsQuery.refetch();
                  }}
                  secretBagSpec={secretBagSpec}
                />
              ))}
            </div>
          )}
          {mcpTools.length > 0 ? (
            <p className="mt-2 text-label text-text-secondary">Saved automatically to this agent.</p>
          ) : null}
        </SectionRow>
      </Section>

      {sharedAttachable.length > 0 ? (
        <Section id="tools-shared" title="Attach a shared tool" description="Tools created from the shared list.">
          <SectionRow className="flex gap-2">
            <Select value={sharedToAttach} onValueChange={setSharedToAttach}>
              <SelectTrigger className="w-full flex-1">
                <SelectValue placeholder="Choose a shared tool" />
              </SelectTrigger>
              <SelectContent>
                {sharedAttachable.map((tool) => (
                  <SelectItem key={tool.id} value={tool.id}>
                    {tool.name} ({tool.kind})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              disabled={sharedToAttach === ""}
              onClick={() => {
                setAttached(sharedToAttach, true);
                setSharedToAttach("");
              }}
            >
              Attach
            </Button>
          </SectionRow>
        </Section>
      ) : null}

      <Section id="tools-pack" title="Pack tools" description="Provided by the agent's pack; not editable here.">
        <SectionRow>
          {packsQuery.isLoading ? (
            <Skeleton className="h-6 w-48" />
          ) : (pack?.tool_names.length ?? 0) === 0 ? (
            <p className="text-sm text-text-secondary">This pack registers no code tools.</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {pack?.tool_names.map((name) => (
                <li
                  key={name}
                  className="rounded-pill bg-muted-strong px-2.5 py-0.5 font-mono text-xs text-foreground"
                >
                  {name}
                </li>
              ))}
            </ul>
          )}
        </SectionRow>
      </Section>
    </div>
  );
}

function BuiltinToolRow({
  tool,
  checked,
  disabledReason,
  onCheckedChange,
  executionChip,
}: {
  tool: BuiltinToolInfo;
  checked: boolean;
  disabledReason: string | null;
  onCheckedChange: (checked: boolean) => void;
  /** The "Execution" chip (BACKGROUNDABLE_BUILTINS only); `null` for every other built-in. */
  executionChip?: React.ReactNode;
}) {
  const id = `builtin-tool-${tool.name}`;
  return (
    <SectionRow>
      <Field
        inline
        label={tool.label}
        htmlFor={id}
        hint={disabledReason ?? tool.help}
      >
        <div className="flex items-center gap-2">
          {executionChip}
          <Switch
            id={id}
            checked={checked && !disabledReason}
            disabled={Boolean(disabledReason)}
            onCheckedChange={onCheckedChange}
            aria-describedby={fieldIds(id).hint}
          />
        </div>
      </Field>
    </SectionRow>
  );
}

/** Site names only, no scheme/path/port (mirrors the api's `_fetch_host_problem`, `config_service.py`). */
const HOST_PATTERN = /^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;

/**
 * `tools.fetch_url_allowed_hosts` (V5-25/V5-28): a chip list of site names,
 * modelled on `LimitsSection`'s `OriginsEditor` but validating a bare
 * hostname (no scheme, no path) instead of an origin — the api refuses
 * anything else at save (`config_service.py::_fetch_host_problem`).
 */
function AllowedHostsEditor({
  value,
  onChange,
  error,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  error?: string;
}) {
  const id = React.useId();
  const [draft, setDraft] = React.useState("");
  const [draftError, setDraftError] = React.useState<string | null>(null);

  function add() {
    const candidate = draft.trim().toLowerCase();
    if (!candidate) return;
    if (candidate.includes("://") || candidate.includes("/") || candidate.includes(":")) {
      setDraftError("Use the site name only (docs.example.com), without https:// or a path.");
      return;
    }
    if (!HOST_PATTERN.test(candidate)) {
      setDraftError("That doesn't look like a site name.");
      return;
    }
    if (!value.includes(candidate)) onChange([...value, candidate]);
    setDraft("");
    setDraftError(null);
  }

  const shownError = draftError ?? error;

  return (
    <div className="flex flex-col gap-3">
      <Field
        label="Add a site"
        htmlFor={id}
        hint="Only pages on these sites — no https:// and no path."
        error={shownError ?? undefined}
      >
        <div className="flex max-w-md gap-2">
          <Input
            id={id}
            value={draft}
            placeholder="docs.example.com"
            autoComplete="off"
            spellCheck={false}
            className="font-mono text-label"
            onChange={(event) => {
              setDraft(event.target.value);
              if (draftError) setDraftError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
            aria-invalid={shownError ? true : undefined}
            data-issue-path="tools.fetch_url_allowed_hosts"
          />
          <Button type="button" variant="outline" onClick={add} disabled={draft.trim() === ""}>
            Add
          </Button>
        </div>
      </Field>
      {value.length > 0 ? (
        <ul aria-label="Allowed sites" className="flex flex-wrap gap-2">
          {value.map((host) => (
            <li
              key={host}
              className="inline-flex h-7 items-center gap-1 rounded-sm border border-border bg-muted pr-0.5 pl-2 font-mono text-xs"
            >
              <span className="max-w-64 truncate">{host}</span>
              <button
                type="button"
                onClick={() => onChange(value.filter((item) => item !== host))}
                aria-label={`Remove ${host}`}
                className="inline-flex size-6 items-center justify-center rounded-sm text-text-secondary outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Icon as={XIcon} size="sm" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-label text-text-secondary">No sites allowed yet: the agent can&apos;t read any web page.</p>
      )}
    </div>
  );
}

/** `tools.notify_team` fresh default — turning the card on starts here; the key is unset until picked. */
const DEFAULT_NOTIFY_TEAM: NotifyTeamConfig = {
  credential_id: "",
  secret_name: "TEAM_WEBHOOK_URL",
  style: "generic",
  on_escalation: false,
  include_transcript: false,
};

/**
 * `tools.notify_team` (V5-25/V5-28): a webhook `notify_team` (and
 * `escalate_to_human`, via "Also notify on escalation") posts a short summary
 * to. The URL itself is never stored in the config — `credential_id` names an
 * `http-tool-secret` key holding it under `secret_name`.
 */
function NotifyTeamCard({
  value,
  onChange,
  secretBagSpec,
  error,
}: {
  value: NotifyTeamConfig | null | undefined;
  onChange: (next: NotifyTeamConfig | null) => void;
  secretBagSpec: ProviderSpec | undefined;
  error?: { message: string; tone: "error" | "warning" };
}) {
  const uid = React.useId();
  // Binding a credential needs `admin` server-side, like the HTTP tool editor's own key field (R-V2-33).
  const { canWrite: canBindCredential } = useWriteAccess("admin");
  const enabled = Boolean(value);

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4" data-issue-path="tools.notify_team">
      <Field
        inline
        label="Tell your team"
        htmlFor={`${uid}-enabled`}
        hint="Posts a short summary to a webhook — Slack or a generic JSON endpoint — for example when the agent escalates to a person."
      >
        <Switch
          id={`${uid}-enabled`}
          checked={enabled}
          onCheckedChange={(checked) => onChange(checked ? DEFAULT_NOTIFY_TEAM : null)}
        />
      </Field>
      {enabled && value ? (
        <div className="flex flex-col gap-4 border-t border-border pt-4">
          {secretBagSpec ? (
            <div
              className={canBindCredential ? undefined : "pointer-events-none opacity-50"}
              aria-disabled={!canBindCredential}
              title={canBindCredential ? undefined : writeAccessReason("admin")}
              data-issue-path="tools.notify_team.credential_id"
            >
              <CredentialPicker
                spec={secretBagSpec}
                value={value.credential_id || null}
                onChange={(id) => onChange({ ...value, credential_id: id ?? "" })}
                label="Team webhook key"
              />
            </div>
          ) : null}
          <Field
            label="Secret name"
            htmlFor={`${uid}-secret-name`}
            hint="The name the webhook URL is stored under in that key."
          >
            <Input
              id={`${uid}-secret-name`}
              value={value.secret_name ?? "TEAM_WEBHOOK_URL"}
              onChange={(event) => onChange({ ...value, secret_name: event.target.value })}
              className="max-w-xs font-mono text-sm"
            />
          </Field>
          <Field label="Format" htmlFor={`${uid}-style`}>
            <Select
              value={value.style ?? "generic"}
              onValueChange={(next) => onChange({ ...value, style: next as "slack" | "generic" })}
            >
              <SelectTrigger id={`${uid}-style`} className="w-full sm:w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="slack">Slack message</SelectItem>
                <SelectItem value="generic">Generic JSON</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field
            inline
            label="Also notify on escalation"
            htmlFor={`${uid}-on-escalation`}
            hint="Posts here too when the agent escalates the call to a person (the Escalation row above)."
          >
            <Switch
              id={`${uid}-on-escalation`}
              checked={value.on_escalation ?? false}
              onCheckedChange={(checked) => onChange({ ...value, on_escalation: checked })}
            />
          </Field>
          <Field
            inline
            label="Include the recent conversation"
            htmlFor={`${uid}-transcript`}
            hint="Off by default: a transcript may hold personal details."
          >
            <Switch
              id={`${uid}-transcript`}
              checked={value.include_transcript ?? false}
              onCheckedChange={(checked) => onChange({ ...value, include_transcript: checked })}
            />
          </Field>
        </div>
      ) : null}
      {error ? (
        <p className={cn("text-label", error.tone === "error" ? "text-destructive-text" : "text-warning-text")}>
          {error.message}
        </p>
      ) : null}
    </div>
  );
}
