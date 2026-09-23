"use client";

import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { FlowEdge, VariableSpec } from "@/contracts/lkap-contracts";

import { MentionTextarea } from "./mention-textarea";

/**
 * The edge editor (V2-16): the natural-language condition (it becomes the
 * `go_to_<target>` tool's description), an optional canvas label, the
 * transition speech spoken before the next step (with `@mention`), and the
 * priority that picks the `max_turns` fallback path.
 */
export function EdgeForm({
  edge,
  sourceLabel,
  targetLabel,
  variables,
  onChange,
  errors = {},
  warnings = {},
}: {
  edge: FlowEdge;
  sourceLabel: string;
  targetLabel: string;
  variables: readonly VariableSpec[];
  onChange: (next: FlowEdge) => void;
  errors?: Readonly<Record<string, string>>;
  warnings?: Readonly<Record<string, string>>;
}) {
  const id = `flow-edge-${edge.id}`;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        From <span className="font-medium text-foreground">{sourceLabel}</span> to{" "}
        <span className="font-medium text-foreground">{targetLabel}</span>
      </p>
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
      <Field label="Label" htmlFor={`${id}-label`} optional hint="Shown on the canvas instead of the condition.">
        <Input
          id={`${id}-label`}
          value={edge.label ?? ""}
          onChange={(event) => onChange({ ...edge, label: event.target.value || null })}
        />
      </Field>
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
    </div>
  );
}
