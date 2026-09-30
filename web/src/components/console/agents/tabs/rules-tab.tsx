"use client";

/**
 * V6-15 (`docs/v6/PLAN-V6.md` V6-15, D-V6-25): the "Rules" section of the agent editor —
 * when → then rows. Each condition is edited with a plain-words builder ("is set",
 * "equals", "is at least", "matches a pattern") that writes the safe grammar
 * (`agents/rules/condition.ts`); a "write it myself" escape hatch drops to a raw textarea,
 * validated live against the same TS port of the grammar the api's Pydantic validator
 * runs, so a bad condition shows the api's own wording inline and blocks the save before
 * any round trip (`lib/schemas.ts`'s `ruleSchema.when`). Action pickers are bound to the
 * agent's own blocks and tool ids — never free-typed paths. No jargon reaches the DOM:
 * "regex", "var.", "tool.", "AST" and "grammar" never appear in a visible string; the
 * builder's operator words are the only vocabulary a reader sees.
 */
import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { PlusIcon, Trash2Icon } from "lucide-react";

import {
  type ConditionRow,
  ConditionError,
  conditionErrorMessage,
  conditionToRows,
  parseCondition,
  rowsToCondition,
} from "@/components/console/agents/rules/condition";
import type { AgentEditorForm, RuleActionForm, RuleForm } from "@/components/console/lib/schemas";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

interface PanelBlockLite {
  id: string;
  type: string;
  title: string | null;
}

const VAR_OPS: { value: ConditionRow["op"]; label: string; needsValue: boolean }[] = [
  { value: "is_set", label: "is set", needsValue: false },
  { value: "is_empty", label: "is empty", needsValue: false },
  { value: "eq", label: "equals", needsValue: true },
  { value: "neq", label: "does not equal", needsValue: true },
  { value: "gte", label: "is at least", needsValue: true },
  { value: "lte", label: "is at most", needsValue: true },
  { value: "gt", label: "is more than", needsValue: true },
  { value: "lt", label: "is less than", needsValue: true },
  { value: "matches", label: "matches a pattern", needsValue: true },
];

const TONE_OPTIONS: { value: "neutral" | "info" | "success" | "warning" | "danger"; label: string }[] = [
  { value: "neutral", label: "Neutral" },
  { value: "info", label: "Informational" },
  { value: "success", label: "Success" },
  { value: "warning", label: "Warning" },
  { value: "danger", label: "Danger" },
];

const ESCALATE_MODE_OPTIONS: { value: "transfer" | "takeover" | "listen_in" | "callback"; label: string }[] = [
  { value: "transfer", label: "Transfer to a person" },
  { value: "takeover", label: "Let a person take over" },
  { value: "listen_in", label: "Have a person listen in" },
  { value: "callback", label: "Arrange a callback" },
];

const ACTION_KINDS: { value: RuleActionForm["do"]; label: string }[] = [
  { value: "checklist.set_item", label: "Add or update a checklist item" },
  { value: "checklist.check", label: "Check off a checklist item" },
  { value: "status.set", label: "Change the status" },
  { value: "details.set", label: "Write into a details block" },
  { value: "note.push", label: "Add a note" },
  { value: "var.set", label: "Set a value" },
  { value: "escalate", label: "Hand off to a person" },
  { value: "instruct", label: "Tell the agent what to do next" },
  { value: "disposition.set", label: "Set the outcome label" },
];

function defaultAction(kind: RuleActionForm["do"]): RuleActionForm {
  switch (kind) {
    case "checklist.set_item":
      return { do: kind, id: "", label: "", done: false, blocking: false, hint: null };
    case "checklist.check":
      return { do: kind, id: "", done: true };
    case "status.set":
      return { do: kind, label: "", tone: "info" };
    case "details.set":
      return { do: kind, block_id: "", key: "", value: "", label: null };
    case "note.push":
      return { do: kind, text: "", block_id: null };
    case "var.set":
      return { do: kind, name: "", value: "" };
    case "escalate":
      return { do: kind, mode: "transfer", reason: "", urgency: "normal" };
    case "instruct":
      return { do: kind, text: "" };
    case "disposition.set":
      return { do: kind, value: "" };
  }
}

function defaultRow(): ConditionRow {
  // A non-empty starting name (rather than "") keeps the joined text parseable the moment
  // the row is added, so the condition stays in builder mode for the caller to rename —
  // an empty `var.` name is a parse error, which would otherwise bounce a fresh row
  // straight into the raw-text fallback before anyone typed anything.
  return { subject: "var", name: "value", op: "is_set" };
}

function emptyRule(taken: readonly string[]): RuleForm {
  let id = "rule";
  for (let n = 1; taken.includes(id); n += 1) id = `rule_${n}`;
  return { id, label: "", when: "", then: [defaultAction("instruct")], once: true, enabled: true };
}

/** One condition row: "A value | tool call" + name + operator + value. */
function ConditionRowEditor({
  row,
  knownVariables,
  knownTools,
  onChange,
  onRemove,
}: {
  row: ConditionRow;
  knownVariables: readonly string[];
  knownTools: readonly string[];
  onChange: (next: ConditionRow) => void;
  onRemove: () => void;
}) {
  const listId = React.useId();
  const opOptions =
    row.subject === "tool"
      ? [
          { value: "tool_ok" as const, label: "succeeded", needsValue: false },
          { value: "tool_failed" as const, label: "failed", needsValue: false },
        ]
      : VAR_OPS;
  const needsValue = opOptions.find((option) => option.value === row.op)?.needsValue ?? false;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value={row.subject}
        onValueChange={(subject) =>
          onChange(subject === "tool" ? { subject: "tool", name: "", op: "tool_ok" } : { subject: "var", name: "", op: "is_set" })
        }
      >
        <SelectTrigger aria-label="What the condition checks" className="w-28">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="var">A value</SelectItem>
          <SelectItem value="tool">A tool call</SelectItem>
        </SelectContent>
      </Select>
      <Input
        aria-label={row.subject === "tool" ? "Tool name" : "Value name"}
        list={listId}
        className="w-40"
        placeholder={row.subject === "tool" ? "tool name" : "value name"}
        value={row.name}
        onChange={(event) => onChange({ ...row, name: event.target.value.trim() })}
      />
      <datalist id={listId}>
        {(row.subject === "tool" ? knownTools : knownVariables).map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <Select value={row.op} onValueChange={(op) => onChange({ ...row, op: op as ConditionRow["op"] })}>
        <SelectTrigger aria-label="Comparison" className="w-44">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {opOptions.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {needsValue ? (
        <Input
          aria-label="Value to compare with"
          className="w-36"
          value={row.value ?? ""}
          onChange={(event) => onChange({ ...row, value: event.target.value })}
        />
      ) : null}
      {row.op === "matches" ? (
        <label className="flex items-center gap-1.5 text-xs">
          <Checkbox checked={row.ignoreCase ?? false} onCheckedChange={(checked) => onChange({ ...row, ignoreCase: checked === true })} />
          Ignore upper/lower case
        </label>
      ) : null}
      <Button type="button" variant="ghost" size="icon-sm" aria-label="Remove this condition" onClick={onRemove}>
        <Icon as={Trash2Icon} size="sm" />
      </Button>
    </div>
  );
}

/**
 * The condition builder for one rule's `when`: rows joined with "and" (the grammar's
 * `and`/`or`/`not`/parentheses stay reachable only through "Write it myself" — see the
 * file header and `condition.ts`'s `conditionToRows`).
 */
function ConditionEditor({
  value,
  onChange,
  knownVariables,
  knownTools,
}: {
  value: string;
  onChange: (next: string) => void;
  knownVariables: readonly string[];
  knownTools: readonly string[];
}) {
  let parseError: ConditionError | null = null;
  let rows: ConditionRow[] | null = [];
  if (value.trim()) {
    try {
      rows = conditionToRows(parseCondition(value));
    } catch (error) {
      rows = null;
      if (error instanceof ConditionError) parseError = error;
    }
  }
  const canBuild = rows !== null;
  const [rawMode, setRawMode] = React.useState(!canBuild);

  if (rawMode || !canBuild) {
    return (
      <div className="flex flex-col gap-1.5">
        <Textarea
          aria-label="Condition"
          rows={2}
          maxLength={200}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        {parseError ? <p className="text-xs text-destructive-text">{conditionErrorMessage(parseError)}</p> : null}
        {canBuild ? (
          <Button type="button" variant="link" size="sm" className="self-start px-0" onClick={() => setRawMode(false)}>
            Use the builder instead
          </Button>
        ) : (
          <p className="text-xs text-text-secondary">
            This condition combines &ldquo;or&rdquo;, &ldquo;not&rdquo; or parentheses, so it&rsquo;s edited as text here.
          </p>
        )}
      </div>
    );
  }

  // Reachable only when `canBuild` (rows !== null); the fallback is for the type checker.
  const activeRows = rows ?? [];
  return (
    <div className="flex flex-col gap-2">
      {activeRows.length === 0 ? <p className="text-xs text-text-secondary">No conditions yet — this rule never fires.</p> : null}
      {activeRows.map((row, index) => (
        <ConditionRowEditor
          key={index}
          row={row}
          knownVariables={knownVariables}
          knownTools={knownTools}
          onChange={(next) => onChange(rowsToCondition(activeRows.map((r, i) => (i === index ? next : r))))}
          onRemove={() => onChange(rowsToCondition(activeRows.filter((_r, i) => i !== index)))}
        />
      ))}
      {activeRows.length > 1 ? <p className="text-xs text-text-secondary">All of these must be true.</p> : null}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange(rowsToCondition([...activeRows, defaultRow()]))}
        >
          <Icon as={PlusIcon} size="sm" /> Add a condition
        </Button>
        <Button type="button" variant="link" size="sm" onClick={() => setRawMode(true)}>
          Write it myself
        </Button>
      </div>
    </div>
  );
}

/** One `then[]` action: a kind picker plus that kind's own fields. */
function ActionEditor({
  action,
  blocks,
  hasChecklist,
  knownVariables,
  onChange,
  onRemove,
}: {
  action: RuleActionForm;
  blocks: readonly PanelBlockLite[];
  hasChecklist: boolean;
  knownVariables: readonly string[];
  onChange: (next: RuleActionForm) => void;
  onRemove: () => void;
}) {
  const detailsBlocks = blocks.filter((block) => block.type === "details");
  const variableListId = React.useId();

  return (
    <div className="flex flex-col gap-2 rounded border border-border p-2.5">
      <div className="flex items-center gap-2">
        <Select value={action.do} onValueChange={(next) => onChange(defaultAction(next as RuleActionForm["do"]))}>
          <SelectTrigger aria-label="Action" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ACTION_KINDS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="button" variant="ghost" size="icon-sm" aria-label="Remove this action" onClick={onRemove}>
          <Icon as={Trash2Icon} size="sm" />
        </Button>
      </div>

      {action.do === "checklist.set_item" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Input aria-label="Item id" placeholder="Item id" value={action.id} onChange={(e) => onChange({ ...action, id: e.target.value.trim() })} />
          <Input aria-label="Item label" placeholder="What it says" value={action.label} onChange={(e) => onChange({ ...action, label: e.target.value })} />
          <label className="flex items-center gap-2 text-xs">
            <Checkbox checked={action.done} onCheckedChange={(c) => onChange({ ...action, done: c === true })} />
            Already checked
          </label>
          <label className="flex items-center gap-2 text-xs">
            <Checkbox checked={action.blocking} onCheckedChange={(c) => onChange({ ...action, blocking: c === true })} />
            Blocks publishing until checked
          </label>
        </div>
      ) : null}
      {!hasChecklist && (action.do === "checklist.set_item" || action.do === "checklist.check") ? (
        <p className="text-xs text-warning-text">Add a checklist block to the panel for this to show.</p>
      ) : null}
      {action.do === "checklist.check" ? (
        <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
          <Input aria-label="Item id" placeholder="Item id" value={action.id} onChange={(e) => onChange({ ...action, id: e.target.value.trim() })} />
          <label className="flex items-center gap-2 text-xs">
            <Checkbox checked={action.done} onCheckedChange={(c) => onChange({ ...action, done: c === true })} />
            Checked
          </label>
        </div>
      ) : null}
      {action.do === "status.set" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Input aria-label="Status text" placeholder="Status" value={action.label} onChange={(e) => onChange({ ...action, label: e.target.value })} />
          <Select value={action.tone} onValueChange={(tone) => onChange({ ...action, tone: tone as typeof action.tone })}>
            <SelectTrigger aria-label="Tone" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TONE_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}
      {action.do === "details.set" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Select value={action.block_id} onValueChange={(blockId) => onChange({ ...action, block_id: blockId })}>
            <SelectTrigger aria-label="Details block" className="w-full">
              <SelectValue placeholder="Choose a block" />
            </SelectTrigger>
            <SelectContent>
              {detailsBlocks.map((block) => (
                <SelectItem key={block.id} value={block.id}>
                  {block.title || block.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input aria-label="Row key" placeholder="Row" value={action.key} onChange={(e) => onChange({ ...action, key: e.target.value.trim() })} />
          <Input
            aria-label="Value"
            className="sm:col-span-2"
            placeholder="Value (may name a captured value)"
            value={action.value}
            onChange={(e) => onChange({ ...action, value: e.target.value })}
          />
          {detailsBlocks.length === 0 ? <p className="text-xs text-warning-text sm:col-span-2">Add a details block to the panel for this to write anywhere.</p> : null}
        </div>
      ) : null}
      {action.do === "note.push" ? (
        <div className="flex flex-col gap-2">
          <Textarea aria-label="Note text" rows={2} value={action.text} onChange={(e) => onChange({ ...action, text: e.target.value })} />
          <Select value={action.block_id ?? "__none__"} onValueChange={(blockId) => onChange({ ...action, block_id: blockId === "__none__" ? null : blockId })}>
            <SelectTrigger aria-label="Show next to" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">General notes</SelectItem>
              {blocks.map((block) => (
                <SelectItem key={block.id} value={block.id}>
                  Next to {block.title || block.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}
      {action.do === "var.set" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Input aria-label="Value name" list={variableListId} value={action.name} onChange={(e) => onChange({ ...action, name: e.target.value.trim() })} />
          <datalist id={variableListId}>
            {knownVariables.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <Input aria-label="New value" value={action.value === null ? "" : String(action.value)} onChange={(e) => onChange({ ...action, value: e.target.value })} />
        </div>
      ) : null}
      {action.do === "escalate" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Select value={action.mode} onValueChange={(mode) => onChange({ ...action, mode: mode as typeof action.mode })}>
            <SelectTrigger aria-label="How" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ESCALATE_MODE_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={action.urgency} onValueChange={(urgency) => onChange({ ...action, urgency: urgency as typeof action.urgency })}>
            <SelectTrigger aria-label="Urgency" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="low">Low</SelectItem>
              <SelectItem value="normal">Normal</SelectItem>
              <SelectItem value="high">High</SelectItem>
            </SelectContent>
          </Select>
          <Textarea
            aria-label="Reason"
            className="sm:col-span-2"
            rows={2}
            placeholder="Why, for whoever picks this up"
            value={action.reason}
            onChange={(e) => onChange({ ...action, reason: e.target.value })}
          />
        </div>
      ) : null}
      {action.do === "instruct" ? (
        <Textarea aria-label="What to tell the agent" rows={2} value={action.text} onChange={(e) => onChange({ ...action, text: e.target.value })} />
      ) : null}
      {action.do === "disposition.set" ? (
        <Input aria-label="Outcome label" value={action.value} onChange={(e) => onChange({ ...action, value: e.target.value })} />
      ) : null}
    </div>
  );
}

function RuleRow({
  rule,
  blocks,
  hasChecklist,
  knownVariables,
  knownTools,
  onChange,
  onRemove,
}: {
  rule: RuleForm;
  blocks: readonly PanelBlockLite[];
  hasChecklist: boolean;
  knownVariables: readonly string[];
  knownTools: readonly string[];
  onChange: (next: RuleForm) => void;
  onRemove: () => void;
}) {
  const base = `rule-${rule.id}`;
  return (
    <div className="flex flex-col gap-3 py-4" data-rule={rule.id}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="grid flex-1 gap-2 sm:grid-cols-[10rem_minmax(0,1fr)]">
          <Field label="Rule id" htmlFor={`${base}-id`}>
            <Input id={`${base}-id`} className="font-mono text-sm" value={rule.id} maxLength={64} onChange={(e) => onChange({ ...rule, id: e.target.value.trim() })} />
          </Field>
          <Field label="What it's for" htmlFor={`${base}-label`} optional>
            <Input id={`${base}-label`} value={rule.label} maxLength={120} onChange={(e) => onChange({ ...rule, label: e.target.value })} />
          </Field>
        </div>
        <div className="flex items-center gap-3 pt-5">
          <label className="flex items-center gap-2 text-xs font-medium">
            <Switch checked={rule.enabled} onCheckedChange={(checked) => onChange({ ...rule, enabled: checked })} aria-label={`${rule.label || rule.id} is on`} />
            {rule.enabled ? "On" : "Off"}
          </label>
          <Button type="button" variant="ghost" size="icon" aria-label={`Remove ${rule.label || rule.id}`} onClick={onRemove}>
            <Icon as={Trash2Icon} size="sm" />
          </Button>
        </div>
      </div>

      <div>
        <span className="text-xs font-medium">When</span>
        <div className="mt-1">
          <ConditionEditor value={rule.when} onChange={(when) => onChange({ ...rule, when })} knownVariables={knownVariables} knownTools={knownTools} />
        </div>
      </div>

      <div>
        <span className="text-xs font-medium">Then</span>
        <div className="mt-1 flex flex-col gap-2">
          {rule.then.map((action, index) => (
            <ActionEditor
              key={index}
              action={action}
              blocks={blocks}
              hasChecklist={hasChecklist}
              knownVariables={knownVariables}
              onChange={(next) => onChange({ ...rule, then: rule.then.map((a, i) => (i === index ? next : a)) })}
              onRemove={() => onChange({ ...rule, then: rule.then.filter((_a, i) => i !== index) })}
            />
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="self-start"
            disabled={rule.then.length >= 10}
            onClick={() => onChange({ ...rule, then: [...rule.then, defaultAction("instruct")] })}
          >
            <Icon as={PlusIcon} size="sm" /> Add an action
          </Button>
        </div>
      </div>

      <label className="flex items-center gap-2 text-xs">
        <Checkbox checked={rule.once} onCheckedChange={(checked) => onChange({ ...rule, once: checked === true })} />
        Fire at most once per call
      </label>
    </div>
  );
}

export function RulesTab() {
  const { control } = useFormContext<AgentEditorForm>();
  const blocks = (useWatch({ control, name: "config.panel.blocks" }) ?? []) as PanelBlockLite[];
  const extractionFields = useWatch({ control, name: "config.extraction.fields" }) ?? [];
  const flowVariables = useWatch({ control, name: "config.flow.variables" }) ?? [];
  const toolIds = useWatch({ control, name: "config.tools.tool_ids" }) ?? [];
  const hasChecklist = blocks.some((block) => block.type === "checklist");

  return (
    <Controller
      control={control}
      name="config.rules"
      render={({ field }) => {
        const rules = field.value ?? [];
        const knownVariables = Array.from(
          new Set([
            ...extractionFields.map((f: { name: string }) => f.name),
            ...flowVariables.map((v: { name: string }) => v.name),
            ...rules.flatMap((rule) => rule.then.filter((a): a is Extract<RuleActionForm, { do: "var.set" }> => a.do === "var.set").map((a) => a.name)),
          ]),
        ).filter(Boolean);

        return (
          <Section
            id="rules"
            title="Rules"
            description='"When" a condition holds, "then" the agent updates the panel, changes the status, hands off to a person, or is told what to do next.'
            aside={
              <Button
                type="button"
                size="sm"
                disabled={rules.length >= 50}
                onClick={() => field.onChange([...rules, emptyRule(rules.map((r) => r.id))])}
              >
                <Icon as={PlusIcon} size="sm" /> Add a rule
              </Button>
            }
          >
            <SectionRow className="flex flex-col divide-y divide-border">
              {rules.length === 0 ? (
                <p className="py-1 text-sm text-text-secondary">No rules yet.</p>
              ) : (
                rules.map((rule, index) => (
                  <RuleRow
                    key={index}
                    rule={rule}
                    blocks={blocks}
                    hasChecklist={hasChecklist}
                    knownVariables={knownVariables}
                    knownTools={toolIds}
                    onChange={(next) => field.onChange(rules.map((r, i) => (i === index ? next : r)))}
                    onRemove={() => field.onChange(rules.filter((_r, i) => i !== index))}
                  />
                ))
              )}
            </SectionRow>
          </Section>
        );
      }}
    />
  );
}
