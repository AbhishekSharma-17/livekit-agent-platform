"use client";

import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { FlowEdge, VariableSpec } from "@/contracts/lkap-contracts";

import { TOOL_OUTCOME_LABEL, type FlowNodeKind, type ToolOutcomeKey } from "./flow-model";
import { MentionTextarea } from "./mention-textarea";

/**
 * The edge editor (V2-16): the natural-language condition (it becomes the
 * `go_to_<target>` tool's description), an optional canvas label, the
 * transition speech spoken before the next step (with `@mention`), and the
 * priority that picks the `max_turns` fallback path.
 *
 * A path leaving a `tool` node (V6-19, D-V6-28, ask #117) is chosen by
 * outcome instead: no condition (the step decides deterministically, not the
 * model), no priority (there is no `max_turns` fallback to pick among them)
 * — `sourceKind`/`outcome` switch this form to a short read-only line, and
 * the assignment itself lives in the tool step's own inspector (`node-form.tsx`'s
 * Outcomes field), the one place that can see every outcome and every path at once.
 */
export function EdgeForm({
  edge,
  sourceLabel,
  targetLabel,
  variables,
  onChange,
  sourceKind,
  outcome,
  errors = {},
  warnings = {},
}: {
  edge: FlowEdge;
  sourceLabel: string;
  targetLabel: string;
  variables: readonly VariableSpec[];
  onChange: (next: FlowEdge) => void;
  sourceKind?: FlowNodeKind;
  outcome?: ToolOutcomeKey | null;
  errors?: Readonly<Record<string, string>>;
  warnings?: Readonly<Record<string, string>>;
}) {
  const id = `flow-edge-${edge.id}`;
  const isToolOutcome = sourceKind === "tool";
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        From <span className="font-medium text-foreground">{sourceLabel}</span> to{" "}
        <span className="font-medium text-foreground">{targetLabel}</span>
      </p>
      {isToolOutcome ? (
        <Field label="Taken when the tool" htmlFor={`${id}-outcome`}>
          <Input id={`${id}-outcome`} value={outcome ? TOOL_OUTCOME_LABEL[outcome] : "Not assigned"} readOnly />
          <p className="mt-1 text-[0.8125rem] text-muted-foreground">
            Change this in the tool step&rsquo;s own Outcomes field.
          </p>
        </Field>
      ) : (
        <Field
          label="Condition"
          htmlFor={`${id}-condition`}
          required
          hint={
            warnings.condition ? (
              <span className="text-warning-text">Warning: {warnings.condition}</span>
            ) : (
              "When the model should take this path, in plain words. It becomes the path tool's description."
            )
          }
          error={errors.condition}
        >
          <Textarea
            id={`${id}-condition`}
            className="min-h-20 resize-y"
            value={edge.condition ?? ""}
            placeholder="The caller has confirmed their policy number."
            onChange={(event) => onChange({ ...edge, condition: event.target.value })}
          />
        </Field>
      )}
      {isToolOutcome ? null : (
        <Field label="Label" htmlFor={`${id}-label`} optional hint="Shown on the canvas instead of the condition.">
          <Input
            id={`${id}-label`}
            value={edge.label ?? ""}
            onChange={(event) => onChange({ ...edge, label: event.target.value || null })}
          />
        </Field>
      )}
      <Field
        label="Transition speech"
        htmlFor={`${id}-speech`}
        optional
        hint={
          warnings.transition_speech ? (
            <span className="text-warning-text">Warning: {warnings.transition_speech}</span>
          ) : (
            "Said before the next step starts. Type @ to insert a variable."
          )
        }
        error={errors.transition_speech}
      >
        <MentionTextarea
          id={`${id}-speech`}
          value={edge.transition_speech ?? ""}
          variables={variables}
          placeholder="Thanks {{ name }}, one moment."
          onValueChange={(next) => onChange({ ...edge, transition_speech: next || null })}
        />
      </Field>
      {isToolOutcome ? null : (
        <Field
          label="Priority"
          htmlFor={`${id}-priority`}
          hint="Higher goes first; the first path is also the fallback when a step's max turns run out."
        >
          <Input
            id={`${id}-priority`}
            type="number"
            step={1}
            value={String(edge.priority ?? 0)}
            onChange={(event) => onChange({ ...edge, priority: Math.trunc(Number(event.target.value) || 0) })}
          />
        </Field>
      )}
    </div>
  );
}
