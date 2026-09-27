"use client";

/**
 * `rule-list.tsx` (V5-41, `docs/v5/PLAN-V5.md` V5-41): one guardrail stage's
 * card — the `input` / `output` / `tool_output` rule list, each row edited a
 * whole rule at a time through `RuleDialog` (a dialog, never a side drawer),
 * the same `tests-section.tsx` precedent every other per-item editor here
 * follows. `useFieldArray`'s own `fields` supplies the stable React key and
 * the structural ops; the real values (including each rule's own `name`,
 * needed for the uniqueness check) come from `useWatch`, the same split
 * `tests-section.tsx` uses and explains.
 */
import * as React from "react";
import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import { displayMessage } from "@/components/console/agents/editor/validation-map";
import type { AgentEditorForm, GuardrailRuleForm } from "@/components/console/lib/schemas";

import { RULE_KIND_LABEL, ruleSummary } from "./kinds";
import { RuleDialog } from "./rule-dialog";

export type GuardrailStage = "input" | "output" | "tool_output";

const STAGE_PATH = {
  input: "config.guardrails.input",
  output: "config.guardrails.output",
  tool_output: "config.guardrails.tool_output",
} as const satisfies Record<GuardrailStage, `config.guardrails.${GuardrailStage}`>;

function RuleRow({
  rule,
  error,
  onEdit,
  onRemove,
}: {
  rule: GuardrailRuleForm;
  error?: string;
  onEdit: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 py-2">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-foreground">
          {rule.name}
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[0.6875rem] font-medium text-muted-foreground">
            {RULE_KIND_LABEL[rule.kind]}
          </span>
        </p>
        <p className="truncate text-[0.8125rem] text-muted-foreground">{ruleSummary(rule)}</p>
        {error ? <p className="mt-0.5 text-[0.8125rem] text-danger-text">{error}</p> : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button type="button" variant="ghost" size="icon" aria-label={`Edit ${rule.name}`} onClick={onEdit}>
          <Icon as={PencilIcon} size="sm" />
        </Button>
        <Button type="button" variant="ghost" size="icon" aria-label={`Remove ${rule.name}`} onClick={onRemove}>
          <Icon as={Trash2Icon} size="sm" />
        </Button>
      </div>
    </div>
  );
}

export interface RuleListCardProps {
  stage: GuardrailStage;
  title: string;
  description: string;
  /** What this stage checks, in a sentence fragment ("what the caller says") — the dialog's own description. */
  checksLabel: string;
}

export function RuleListCard({ stage, title, description, checksLabel }: RuleListCardProps) {
  const { control } = useFormContext<AgentEditorForm>();
  const name = STAGE_PATH[stage];
  const { fields, append, update, remove } = useFieldArray({ control, name });
  const values = useWatch({ control, name }) ?? [];
  const { issueFor } = useSectionIssues("guardrails");
  const [dialogIndex, setDialogIndex] = React.useState<number | "new" | null>(null);

  const editingIndex = typeof dialogIndex === "number" ? dialogIndex : null;
  const editing = editingIndex !== null ? (values[editingIndex] ?? null) : null;
  const otherNames = values.filter((_, i) => i !== editingIndex).map((rule) => rule?.name ?? "");
  const dialogIssue = editingIndex !== null ? issueFor(`guardrails.${stage}.${editingIndex}`) : undefined;

  return (
    <Section
      id={`guardrails-${stage}`}
      title={title}
      description={description}
      aside={
        <Button type="button" size="sm" onClick={() => setDialogIndex("new")} disabled={fields.length >= 20}>
          <Icon as={PlusIcon} size="sm" /> Add rule
        </Button>
      }
    >
      <SectionRow className="flex flex-col divide-y divide-border">
        {fields.length === 0 ? (
          <p className="py-1 text-sm text-muted-foreground">No rules yet.</p>
        ) : (
          fields.map((field, index) => {
            const rule = values[index];
            if (!rule) return null;
            const issue = issueFor(`guardrails.${stage}.${index}`);
            return (
              <RuleRow
                key={field.id}
                rule={rule}
                error={issue ? displayMessage(issue) : undefined}
                onEdit={() => setDialogIndex(index)}
                onRemove={() => remove(index)}
              />
            );
          })
        )}
      </SectionRow>
      <RuleDialog
        open={dialogIndex !== null}
        onOpenChange={(open) => {
          if (!open) setDialogIndex(null);
        }}
        initial={editing}
        otherNames={otherNames}
        stageLabel={checksLabel}
        serverError={dialogIssue ? displayMessage(dialogIssue) : undefined}
        onSave={(value) => {
          if (editingIndex !== null) update(editingIndex, value);
          else append(value);
        }}
      />
    </Section>
  );
}
