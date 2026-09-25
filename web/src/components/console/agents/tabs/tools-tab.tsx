"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";
import { ChevronRightIcon, PlusIcon } from "lucide-react";

import { Field, fieldIds } from "@/components/shared/field";
import { Section, SectionRow } from "@/components/shared/section";
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
import { HttpToolEditorDialog } from "@/components/console/tools/http-tool-editor-dialog";
import { McpToolEditorDialog } from "@/components/console/tools/mcp-tool-editor-dialog";
import { ToolRow } from "@/components/console/tools/tool-row";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { cn } from "@/lib/utils";
import type { AgentOut, ToolExecution, ToolOut } from "@/contracts/lkap-contracts";

/**
 * Built-in tool groups (§4.7): the tool names come from WP-0's
 * `BUILTIN_TOOLS` (`constants.ts`, not owned by this package), grouped here
 * since the contract carries no grouping of its own.
 */
const BUILTIN_GROUPS: { label: string; tools: string[] }[] = [
  { label: "Conversation", tools: ["end_call", "current_time"] },
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

  const secretBagSpec = providersQuery.data?.providers.find((p) => p.kind === "secret_bag");
  const pack = packsQuery.data?.items.find((p) => p.manifest.id === agent.pack_id)?.manifest;

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
          <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs">
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
  const mcpTools = ownTools.filter((t) => t.kind === "mcp");
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
                <p className="text-xs font-medium text-muted-foreground">{group.label}</p>
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
          <p className="text-xs font-medium text-muted-foreground">Network</p>
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
          <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
            <CollapsibleTrigger
              className={cn(
                "group/more inline-flex items-center gap-1 rounded-xs text-[0.8125rem] font-medium text-muted-foreground outline-none",
                "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <ChevronRightIcon
                className="size-3.5 transition-transform duration-(--dur-2) group-data-[state=open]/more:rotate-90"
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

      <Section
        id="tools-http"
        title="HTTP tools"
        description="Custom calls to an external API, exposed to the model as a function."
        aside={
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
            <p className="mt-2 text-[0.8125rem] text-muted-foreground">Saved automatically to this agent.</p>
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
            <p className="mt-2 text-[0.8125rem] text-muted-foreground">Saved automatically to this agent.</p>
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
            <p className="text-sm text-muted-foreground">This pack registers no code tools.</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {pack?.tool_names.map((name) => (
                <li
                  key={name}
                  className="rounded-full bg-secondary px-2.5 py-0.5 font-mono text-xs text-secondary-foreground"
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
