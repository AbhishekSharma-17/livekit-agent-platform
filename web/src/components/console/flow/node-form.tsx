"use client";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { BindingsEditor } from "@/components/console/tools/bindings-editor";
import { PinnedArgumentsEditor, type PinnedValue } from "@/components/console/tools/pinned-arguments-editor";
import type { ProviderRef, ToolBinding, ToolNode, VariableSpec } from "@/contracts/lkap-contracts";
import { SchemaForm, type FieldRendererProps, type JsonSchema } from "@/lib/schema-form";
import { cn } from "@/lib/utils";

import {
  kindOf,
  setToolOutcome,
  toolArgumentIssues,
  TOOL_OUTCOME_KEYS,
  TOOL_OUTCOME_LABEL,
  type AnyFlowNode,
  type ToolOutcomeKey,
} from "./flow-model";
import { MentionTextarea } from "./mention-textarea";
import type { NodeToolOption, ToolNodeOption } from "./tool-options";
import type { KbScope } from "./use-node-options";

/**
 * One flow node's form (V2-16), generated from the node kind's JSON schema
 * (`GET /v1/flows/node-specs`) by `SchemaForm`. Every property renders; the
 * builder swaps in richer controls for a few of them: templated text gets
 * `@mention` insertion, `tools` / `kb_ids` pick only from the agent-level
 * union (R-V2-10), `extract` picks flow variables, `providers` is a per-slot
 * override picker (cascaded only, R-V2-9). No react-query here, so tests can
 * render it with plain props.
 */

export interface Option {
  value: string;
  label: string;
  group?: string;
  hint?: string;
}

export interface ProviderOption {
  id: string;
  label: string;
  kind: string;
  defaultModel?: string | null;
}

export interface NodeFormProps {
  node: AnyFlowNode;
  schema: JsonSchema;
  onChange: (next: AnyFlowNode) => void;
  variables: readonly VariableSpec[];
  toolOptions: readonly NodeToolOption[];
  kbOptions: readonly Option[];
  providerOptions: readonly ProviderOption[];
  /** Field → error message. */
  errors?: Readonly<Record<string, string>>;
  /** Field → warning message (shown in the hint slot). */
  warnings?: Readonly<Record<string, string>>;
  onManageVariables?: () => void;
  /** What this step searches (R-V4-29); omitted → the knowledge field shows no scope note. */
  kbScope?: KbScope | null;
  /** V6-19 (ask #117): a `tool` node's own tool picker — the agent's attached rows, no built-ins. */
  toolStepOptions?: readonly ToolNodeOption[];
  /** V6-19: `details`/`table` blocks a `tool` node's bindings may target (`use-node-options.ts`). */
  detailsBlocks?: readonly { id: string; title: string; fieldKeys: string[] }[];
  tableBlocks?: readonly { id: string; title: string }[];
  /** V6-19: the edges leaving this `tool` node, for its Outcomes picker. */
  edgeOptions?: readonly { id: string; label: string }[];
}

const TEMPLATED = ["instructions", "greeting", "farewell", "announce", "rubric_prompt"] as const;

const LABELS: Record<string, string> = {
  id: "Id",
  kind: "Kind",
  label: "Name",
  position: "Position",
  instructions: "Instructions",
  tools: "Tools",
  kb_ids: "Knowledge bases",
  extract: "Collect on exit",
  allow_interruptions: "Caller can interrupt",
  providers: "Model overrides",
  max_turns: "Max turns in this step",
  greeting: "Greeting",
  greeting_mode: "Greeting mode",
  farewell: "Farewell",
  disposition: "Disposition",
  webhook_event: "Record the flow end",
  to: "Transfer to",
  mode: "Transfer mode",
  announce: "Announcement",
  rubric_prompt: "Scoring rubric",
};

const HINTS: Record<string, string> = {
  id: "Fixed once created — the model's path tools are named after it (go_to_<id>).",
  position: "Drag the node on the canvas to move it.",
  instructions: "What the agent does in this step. Type @ to insert a variable.",
  tools: "Only the agent's own tools can be used in a step.",
  kb_ids:
    "A step searches what the Global node lists plus what is picked here; when no step lists anything, every step searches all of the agent's knowledge bases.",
  extract: "Variables filled from the conversation when the call leaves this step.",
  max_turns: "After this many caller turns without moving on, the first path is taken.",
  providers: "Cascaded pipelines only. Same provider as the pipeline (same key) or a provider that needs no key.",
  greeting: "Spoken when the call starts. Type @ to insert a variable.",
  farewell: "Spoken before the call ends. Type @ to insert a variable.",
  disposition: "Stored on the session and sent with the session.ended webhook.",
  announce: "Spoken before the transfer. Type @ to insert a variable.",
  rubric_prompt: "Replaces the agent's QA rubric for flow sessions. Any QA node turns QA on.",
  webhook_event: "Reserved: records the end state in the flow_ended event.",
};

/** V6-19: the `tool` node's own fields (D-V6-28) — handled outside `SchemaForm`'s generic
 * per-property rendering (`TOOL_NODE_FIELDS`, hidden below) since they interlock: picking a
 * tool reseeds its arguments and clears `mcp_tool`, and the outcome picker needs the node's
 * outgoing edges, not just this node's own value. */
const TOOL_NODE_FIELDS = ["tool", "mcp_tool", "arguments", "bindings", "on"] as const;

export function NodeForm({
  node,
  schema,
  onChange,
  variables,
  toolOptions,
  kbOptions,
  providerOptions,
  errors = {},
  warnings = {},
  onManageVariables,
  kbScope,
  toolStepOptions = [],
  detailsBlocks = [],
  tableBlocks = [],
  edgeOptions = [],
}: NodeFormProps) {
  const kind = kindOf(node);
  const value = node as unknown as Record<string, unknown>;

  const renderers = React.useMemo(() => {
    const out: Record<string, (props: FieldRendererProps) => React.ReactNode> = {
      id: ({ id, value: current }) => <Input id={id} value={String(current ?? "")} readOnly />,
      position: ({ id, value: current }) => {
        const [x, y] = Array.isArray(current) ? current : [0, 0];
        return <Input id={id} value={`x ${Math.round(Number(x))} · y ${Math.round(Number(y))}`} readOnly />;
      },
      tools: ({ id, value: current, onChange: set, describedBy }) => (
        <CheckboxList
          id={id}
          describedBy={describedBy}
          options={toolOptions.map((option) => ({ value: option.name, label: option.label, group: option.group }))}
          value={asStrings(current)}
          onChange={set}
          empty="This agent has no tools to pick from."
        />
      ),
      kb_ids: ({ id, value: current, onChange: set, describedBy }) => (
        <div className="flex flex-col gap-2">
          {kbScope ? <KbScopeNote id={`${id}-scope`} scope={kbScope} kbOptions={kbOptions} isGlobal={kind === "global"} /> : null}
          <CheckboxList
            id={id}
            describedBy={describedBy}
            options={kbOptions}
            value={asStrings(current)}
            onChange={set}
            empty="Attach knowledge bases to the agent first (Knowledge section)."
          />
        </div>
      ),
      extract: ({ id, value: current, onChange: set, describedBy }) => (
        <div className="flex flex-col gap-2">
          <CheckboxList
            id={id}
            describedBy={describedBy}
            options={variables.map((variable) => ({
              value: variable.name,
              label: variable.name,
              hint: variable.description || variable.type,
            }))}
            value={asStrings(current)}
            onChange={set}
            empty="No variables yet."
          />
          {onManageVariables ? (
            <button
              type="button"
              className="self-start text-xs font-medium text-foreground underline underline-offset-2"
              onClick={onManageVariables}
            >
              Manage variables
            </button>
          ) : null}
        </div>
      ),
      providers: ({ id, value: current, onChange: set, describedBy }) => (
        <ProviderOverrides
          id={id}
          describedBy={describedBy}
          value={(current as Record<string, ProviderRef> | undefined) ?? {}}
          options={providerOptions}
          onChange={set}
        />
      ),
    };
    for (const name of TEMPLATED) {
      out[name] = ({ id, value: current, onChange: set, field, describedBy, invalid }) => (
        <MentionTextarea
          id={id}
          value={typeof current === "string" ? current : ""}
          onValueChange={(next) => set(next === "" && field.nullable ? null : next)}
          variables={variables}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
        />
      );
    }
    return out;
  }, [kbOptions, kbScope, kind, onManageVariables, providerOptions, toolOptions, variables]);

  return (
    <div className="flex flex-col gap-4">
      <SchemaForm
        key={`${node.id}-${kind}`}
        schema={schema}
        value={value}
        onChange={(next) => onChange(next as unknown as AnyFlowNode)}
        idPrefix={`flow-node-${node.id}`}
        readOnly={["id", "kind"]}
        hidden={kind === "tool" ? TOOL_NODE_FIELDS : []}
        labels={LABELS}
        hints={withWarnings(HINTS, warnings)}
        errors={errors}
        renderers={renderers}
      />
      {kind === "tool" ? (
        <ToolStepFields
          node={node as ToolNode}
          onChange={(next) => onChange(next)}
          toolOptions={toolStepOptions}
          detailsBlocks={detailsBlocks}
          tableBlocks={tableBlocks}
          edgeOptions={edgeOptions}
          variableNames={variables.map((variable) => variable.name)}
          errors={errors}
        />
      ) : null}
    </div>
  );
}

/**
 * The `tool` node's own section (V6-19, ask #117): tool picker (the agent's attached rows
 * only, no built-ins), a server tool sub-picker when the chosen row is an MCP server,
 * argument templates (the V6-11 "Insert value" picker, `ctx`/`var` only — a bare `{{ name }}`
 * is refused, D-V6-28), bindings (V6-11's `BindingsEditor`), and the outcome edges
 * (`on.ok`/`.error`/`.empty`, worded "Found" / "Nothing found" / "Failed").
 */
function ToolStepFields({
  node,
  onChange,
  toolOptions,
  detailsBlocks,
  tableBlocks,
  edgeOptions,
  variableNames,
  errors,
}: {
  node: ToolNode;
  onChange: (next: ToolNode) => void;
  toolOptions: readonly ToolNodeOption[];
  detailsBlocks: readonly { id: string; title: string; fieldKeys: string[] }[];
  tableBlocks: readonly { id: string; title: string }[];
  edgeOptions: readonly { id: string; label: string }[];
  variableNames: readonly string[];
  errors: Readonly<Record<string, string>>;
}) {
  const uid = React.useId();
  const selected = toolOptions.find((option) => option.name === node.tool);
  const arguments_ = (node.arguments ?? {}) as Record<string, PinnedValue>;

  function pickTool(name: string) {
    const option = toolOptions.find((candidate) => candidate.name === name);
    const seeded: Record<string, PinnedValue> = {};
    for (const argumentName of option?.argumentNames ?? []) seeded[argumentName] = "";
    onChange({ ...node, tool: name, mcp_tool: null, arguments: seeded });
  }

  return (
    <div className="flex flex-col gap-5 border-t border-border pt-4">
      <Field label="Tool" htmlFor={`${uid}-tool`} required error={errors.tool}>
        <Select value={node.tool || undefined} onValueChange={pickTool}>
          <SelectTrigger id={`${uid}-tool`} className="w-full">
            <SelectValue placeholder="Choose a tool…" />
          </SelectTrigger>
          <SelectContent>
            {toolOptions.map((option) => (
              <SelectItem key={option.name} value={option.name}>
                {option.label}
              </SelectItem>
            ))}
            {node.tool && !selected ? (
              <SelectItem value={node.tool}>{node.tool} (not attached)</SelectItem>
            ) : null}
          </SelectContent>
        </Select>
        {toolOptions.length === 0 ? (
          <p className="mt-1 text-label text-text-secondary">
            Attach a tool to this agent first, in its Tools section.
          </p>
        ) : null}
      </Field>

      {selected?.kind === "mcp" ? (
        <Field label="Server tool" htmlFor={`${uid}-mcp-tool`} hint="Which of the server's tools this step calls.">
          <Select value={node.mcp_tool ?? undefined} onValueChange={(next) => onChange({ ...node, mcp_tool: next })}>
            <SelectTrigger id={`${uid}-mcp-tool`} className="w-full">
              <SelectValue placeholder="Choose…" />
            </SelectTrigger>
            <SelectContent>
              {(selected.mcpToolNames ?? []).map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      ) : null}

      <PinnedArgumentsEditor
        title="Arguments"
        description="What this step passes to the tool. A value can use {{ ctx.* }} (a session fact) or {{ var.* }} (a flow variable)."
        emptyLabel="This tool takes no arguments, or none are set yet."
        values={arguments_}
        onChange={(next) => onChange({ ...node, arguments: next })}
        variableNames={variableNames}
        issuesOf={(pinned) =>
          Object.entries(pinned).flatMap(([name, argumentValue]) =>
            typeof argumentValue === "string"
              ? toolArgumentIssues(argumentValue).map((issue) => `${name}: ${issue}`)
              : [],
          )
        }
      />

      <BindingsEditor
        uid={uid}
        values={node.bindings ?? []}
        onChange={(bindings: ToolBinding[]) => onChange({ ...node, bindings })}
        detailsBlocks={[...detailsBlocks]}
        tableBlocks={[...tableBlocks]}
      />

      <ToolOutcomesField node={node} edgeOptions={edgeOptions} onChange={(on) => onChange({ ...node, on })} error={errors.on} />
    </div>
  );
}

/** `ToolNode.on` (D-V6-28): which outgoing edge is taken for each outcome. */
function ToolOutcomesField({
  node,
  edgeOptions,
  onChange,
  error,
}: {
  node: ToolNode;
  edgeOptions: readonly { id: string; label: string }[];
  onChange: (next: ToolNode["on"]) => void;
  error?: string;
}) {
  const uid = React.useId();
  const NONE = "__none__";
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium text-foreground">Outcomes</h3>
        <p className="text-label text-pretty text-text-secondary">
          Which path this step takes next, depending on how the call went.
        </p>
      </div>
      {edgeOptions.length === 0 ? (
        <p className="text-label text-text-secondary">
          Draw a path from this step on the canvas, then assign it to an outcome here.
        </p>
      ) : (
        (["ok", "empty", "error"] as ToolOutcomeKey[]).map((outcome) => {
          const value = node.on?.[outcome] ?? "";
          const stale = value !== "" && !edgeOptions.some((edge) => edge.id === value);
          const fieldId = `${uid}-outcome-${outcome}`;
          return (
            <Field
              key={outcome}
              label={TOOL_OUTCOME_LABEL[outcome]}
              htmlFor={fieldId}
              required={outcome === "ok"}
              optional={outcome !== "ok"}
              hint={
                outcome === "empty"
                  ? "Without one, Found's path is taken."
                  : outcome === "error"
                    ? "Without one, the call ends with a short apology."
                    : undefined
              }
              error={outcome === "ok" ? error : undefined}
            >
              <Select
                value={value || NONE}
                onValueChange={(next) => onChange(setToolOutcome(node.on, outcome, next === NONE ? null : next))}
              >
                <SelectTrigger id={fieldId} className="w-full">
                  <SelectValue placeholder="Not assigned" />
                </SelectTrigger>
                <SelectContent>
                  {outcome !== "ok" ? <SelectItem value={NONE}>Not assigned</SelectItem> : null}
                  {edgeOptions.map((edge) => {
                    const takenBy = TOOL_OUTCOME_KEYS.filter(
                      (other) => other !== outcome && (node.on?.[other] ?? "") === edge.id,
                    );
                    return (
                      <SelectItem key={edge.id} value={edge.id}>
                        {edge.label}
                        {takenBy.length > 0 ? ` (also ${takenBy.map((o) => TOOL_OUTCOME_LABEL[o]).join(", ")})` : ""}
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
              {stale ? <p className="mt-1 text-label text-destructive-text">This path no longer exists.</p> : null}
            </Field>
          );
        })
      )}
    </div>
  );
}

/** Warnings replace the field's hint, marked so they read as a finding, not help text. */
function withWarnings(
  hints: Readonly<Record<string, string>>,
  warnings: Readonly<Record<string, string>>,
): Record<string, React.ReactNode> {
  const out: Record<string, React.ReactNode> = { ...hints };
  for (const [field, message] of Object.entries(warnings)) {
    out[field] = <span className="text-warning-text">Warning: {message}</span>;
  }
  return out;
}

/**
 * The knowledge field's scope line (R-V4-29): everything is inherited, the
 * Global node's picks this step gets too, or nothing at all.
 */
function KbScopeNote({
  id,
  scope,
  kbOptions,
  isGlobal,
}: {
  id: string;
  scope: KbScope;
  kbOptions: readonly Option[];
  isGlobal: boolean;
}) {
  const nameOf = (kbId: string) => kbOptions.find((option) => option.value === kbId)?.label ?? kbId;
  if (scope.state === "inherits") {
    const count = scope.ids.length;
    if (count === 0) return null;
    const inherits =
      count === 1 ? "Inherits the one knowledge base of this agent." : `Inherits all ${count} knowledge bases of this agent.`;
    const narrow = isGlobal ? "Pick some here to narrow every step." : "Pick some here or on the Global node to narrow.";
    return (
      <p id={id} data-kb-scope="inherits" className="text-label text-text-secondary">
        {inherits} {narrow}
      </p>
    );
  }
  if (scope.state === "none") {
    return (
      <p id={id} data-kb-scope="none" className="text-label text-warning-text">
        {isGlobal
          ? "Adds no knowledge base to the steps; each step searches only its own picks."
          : "This step searches no knowledge base."}
      </p>
    );
  }
  if (scope.fromGlobal.length === 0) return null;
  return (
    <ul id={id} data-kb-scope="scoped" aria-label="Knowledge bases from the Global node" className="flex flex-wrap gap-1.5">
      {scope.fromGlobal.map((kbId) => (
        <li key={kbId}>
          <Badge tone="neutral">From Global: {nameOf(kbId)}</Badge>
        </li>
      ))}
    </ul>
  );
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

/** Checkbox list; selected values missing from `options` stay visible (flagged) so they can be removed. */
export function CheckboxList({
  id,
  options,
  value,
  onChange,
  empty,
  describedBy,
}: {
  id: string;
  options: readonly Option[];
  value: readonly string[];
  onChange: (next: string[]) => void;
  empty: string;
  describedBy?: string;
}) {
  const known = new Set(options.map((option) => option.value));
  const unknown = value.filter((item) => !known.has(item));
  const all: (Option & { unknown?: boolean })[] = [
    ...options,
    ...unknown.map((item) => ({ value: item, label: item, hint: "Not available to this agent", unknown: true })),
  ];
  if (all.length === 0) {
    return (
      <p id={id} className="text-label text-text-secondary" aria-describedby={describedBy}>
        {empty}
      </p>
    );
  }
  const toggle = (item: string, checked: boolean) =>
    onChange(checked ? [...value, item] : value.filter((existing) => existing !== item));
  return (
    <div id={id} role="group" aria-describedby={describedBy} className="flex flex-col gap-1.5">
      {all.map((option, index) => {
        const checkboxId = `${id}-${index}`;
        return (
          <div key={option.value} className="flex items-start gap-2">
            <Checkbox
              id={checkboxId}
              className="mt-0.5"
              checked={value.includes(option.value)}
              onCheckedChange={(checked) => toggle(option.value, checked === true)}
            />
            <label htmlFor={checkboxId} className="flex min-w-0 flex-col text-sm leading-5">
              <span className={cn("truncate", option.unknown && "text-destructive-text")}>
                {option.label}
                {option.group ? <span className="ml-1.5 text-xs text-text-secondary">{option.group}</span> : null}
              </span>
              {option.hint ? <span className="text-xs text-text-secondary">{option.hint}</span> : null}
            </label>
          </div>
        );
      })}
    </div>
  );
}

const DEFAULT = "__session__";

/** Per-slot (`llm`, `tts`) provider + model override; the credential is inherited (R-V2-9). */
function ProviderOverrides({
  id,
  value,
  options,
  onChange,
  describedBy,
}: {
  id: string;
  value: Record<string, ProviderRef>;
  options: readonly ProviderOption[];
  onChange: (next: Record<string, ProviderRef>) => void;
  describedBy?: string;
}) {
  const set = (slot: "llm" | "tts", ref: ProviderRef | null) => {
    const next = { ...value };
    if (ref) next[slot] = ref;
    else delete next[slot];
    onChange(next);
  };
  return (
    <div id={id} role="group" aria-describedby={describedBy} className="flex flex-col gap-3">
      {(["llm", "tts"] as const).map((slot) => {
        const current = value[slot];
        const slotOptions = options.filter((option) => option.kind === slot);
        return (
          <div key={slot} className="grid grid-cols-[3rem_minmax(0,1fr)] items-center gap-2">
            <span className="text-xs font-medium uppercase text-text-secondary">{slot}</span>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Select
                value={current?.provider_id ?? DEFAULT}
                onValueChange={(next) => set(slot, next === DEFAULT ? null : { provider_id: next, model: null })}
              >
                <SelectTrigger id={`${id}-${slot}`} aria-label={`${slot} provider`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={DEFAULT}>Session default</SelectItem>
                  {slotOptions.map((option) => (
                    <SelectItem key={option.id} value={option.id}>
                      {option.label}
                    </SelectItem>
                  ))}
                  {current && !slotOptions.some((option) => option.id === current.provider_id) ? (
                    <SelectItem value={current.provider_id}>{current.provider_id}</SelectItem>
                  ) : null}
                </SelectContent>
              </Select>
              {current ? (
                <Input
                  aria-label={`${slot} model`}
                  placeholder={
                    slotOptions.find((option) => option.id === current.provider_id)?.defaultModel ?? "Default model"
                  }
                  value={current.model ?? ""}
                  onChange={(event) => set(slot, { ...current, model: event.target.value || null })}
                />
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
