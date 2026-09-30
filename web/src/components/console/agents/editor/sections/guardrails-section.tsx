"use client";

/**
 * "Guardrails" (V5-41, `docs/v5/PLAN-V5.md` V5-41, ask #280): rules on what
 * the caller says, what the agent says and what a tool returns —
 * `config.guardrails`. Off by default: every list starts empty, which the
 * contract (`GuardrailsConfig.active`) and the worker (V5-39) both read as
 * "no check, no added latency" — there is no separate on/off switch to add
 * here, the empty lists already mean off.
 *
 * Plain wording throughout (§0.1): "regex" never appears in a heading — the
 * three rule lists are titled by what they check, and a rule's own kind is
 * always spelled "Pattern" / "Instruction" / "Moderation service"
 * (`agents/guardrails/kinds.ts`).
 */
import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";

import { Field } from "@/components/shared/field";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Section, SectionRow } from "@/components/shared/section";
import { Textarea } from "@/components/ui/textarea";
import { ProviderSlotCard } from "@/components/console/registry/provider-slot-card";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

import { RuleListCard } from "@/components/console/agents/guardrails/rule-list";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import { displayMessage } from "@/components/console/agents/editor/validation-map";

import { describedBy } from "./field-aria";

type OnTrip = NonNullable<AgentEditorForm["config"]["guardrails"]>["on_trip"];

const ON_TRIP_OPTIONS: { value: OnTrip; label: string; hint: string }[] = [
  {
    value: "interrupt",
    label: "Say the safe reply",
    hint: "Stops what it was doing, says the safe reply below, then keeps talking with the caller.",
  },
  {
    value: "end_call",
    label: "Say the safe reply, then end the call",
    hint: "Same as above, and the call ends once the safe reply is spoken.",
  },
  {
    value: "escalate",
    label: "Say the safe reply, then hand to a person",
    hint: "Same as above, and a person is asked to take over.",
  },
];

export function GuardrailsSection() {
  const { control } = useFormContext<AgentEditorForm>();
  const { issueFor } = useSectionIssues("guardrails");
  const safeReplyIssue = issueFor("guardrails.safe_reply");
  const onTripIssue = issueFor("guardrails.on_trip");
  const [modelExpanded, setModelExpanded] = React.useState(false);
  const budgetMs = useWatch({ control, name: "config.guardrails.budget_ms" });

  return (
    <div className="flex flex-col gap-6">
      <RuleListCard
        stage="input"
        title="What the caller says"
        description="Checked once the caller finishes speaking, before the agent answers. A trip drops that turn — the caller's words never reach the transcript."
        checksLabel="what the caller says"
      />
      <RuleListCard
        stage="output"
        title="What the agent says"
        description="Checked as the agent speaks, one sentence at a time. A trip stops the agent mid-reply."
        checksLabel="what the agent says"
      />
      <RuleListCard
        stage="tool_output"
        title="What tools return"
        description="Checked before a tool's result reaches the agent. A trip always replaces the result with the safe reply below — the agent never sees it."
        checksLabel="what a tool returns"
      />

      <Section
        id="guardrails-response"
        title="On a trip"
        description="What happens when a rule above trips, and what the agent says."
      >
        <SectionRow>
          <Field
            label="Safe reply"
            htmlFor="guardrails-safe-reply"
            hint="What the agent says, word for word."
            error={safeReplyIssue ? displayMessage(safeReplyIssue) : undefined}
          >
            <Controller
              control={control}
              name="config.guardrails.safe_reply"
              render={({ field, fieldState }) => (
                <Textarea
                  id="guardrails-safe-reply"
                  rows={2}
                  maxLength={500}
                  value={field.value}
                  onChange={field.onChange}
                  aria-invalid={fieldState.error || safeReplyIssue ? true : undefined}
                  data-issue-path="guardrails.safe_reply"
                />
              )}
            />
          </Field>
        </SectionRow>
        <SectionRow>
          <fieldset className="flex flex-col gap-2.5">
            <legend className="text-sm font-medium text-foreground">
              What happens next, for what the caller or the agent says
            </legend>
            <p className="text-label text-text-secondary">
              A tool result that trips is always replaced by the safe reply above — this choice doesn&apos;t
              change that.
            </p>
            <Controller
              control={control}
              name="config.guardrails.on_trip"
              render={({ field }) => (
                <RadioGroup
                  value={field.value}
                  onValueChange={field.onChange}
                  aria-label="What happens next"
                  data-issue-path="guardrails.on_trip"
                  className="flex flex-col gap-2.5"
                >
                  {ON_TRIP_OPTIONS.map((option) => {
                    const id = `guardrails-on-trip-${option.value}`;
                    return (
                      <label
                        key={option.value}
                        htmlFor={id}
                        className="flex items-start gap-2.5 rounded border border-border p-3 text-sm has-[:checked]:border-brand-border has-[:checked]:bg-brand-subtle/40"
                      >
                        <RadioGroupItem id={id} value={option.value} className="mt-0.5" />
                        <span className="flex flex-col gap-0.5">
                          <span className="font-medium text-foreground">{option.label}</span>
                          <span className="text-label text-pretty text-text-secondary">{option.hint}</span>
                        </span>
                      </label>
                    );
                  })}
                </RadioGroup>
              )}
            />
            {onTripIssue ? (
              <p className="text-label text-warning-text">{displayMessage(onTripIssue)}</p>
            ) : null}
          </fieldset>
        </SectionRow>
      </Section>

      <Section
        id="guardrails-checking"
        title="How it checks"
        description="The Instruction and Moderation service kinds ask a model or a hosted service, which takes a moment; a Pattern is instant and never affected by this."
      >
        <SectionRow>
          <Controller
            control={control}
            name="config.guardrails.model"
            render={({ field }) => (
              <ProviderSlotCard
                title="Model for Instruction rules"
                description="Judges each Instruction rule against the text. Leave unset to use this agent's own workflow model."
                kind="llm"
                value={field.value ?? null}
                onChange={field.onChange}
                expanded={modelExpanded}
                onExpandedChange={setModelExpanded}
                onRemove={field.value ? () => field.onChange(null) : undefined}
                issuePath="guardrails.model"
              />
            )}
          />
        </SectionRow>
        <SectionRow>
          <Field
            label="Time budget"
            htmlFor="guardrails-budget"
            hint="If an Instruction or Moderation service check takes longer than this, the text goes through unchecked rather than making the caller wait — a slow check never blocks the call."
          >
            <Controller
              control={control}
              name="config.guardrails.budget_ms"
              render={({ field }) => (
                <InputGroup className="max-w-40">
                  <InputGroupInput
                    id="guardrails-budget"
                    type="number"
                    inputMode="numeric"
                    min={50}
                    max={2000}
                    value={typeof field.value === "number" && Number.isFinite(field.value) ? field.value : ""}
                    onBlur={field.onBlur}
                    onChange={(event) =>
                      field.onChange(event.target.value === "" ? Number.NaN : Number(event.target.value))
                    }
                    aria-describedby={describedBy("guardrails-budget")}
                    data-issue-path="guardrails.budget_ms"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText>ms</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              )}
            />
          </Field>
          {Number.isFinite(budgetMs) && (budgetMs as number) < 200 ? (
            <p className="mt-1.5 text-label text-warning-text">
              A very short budget means these checks fail open (let the text through) more often.
            </p>
          ) : null}
        </SectionRow>
      </Section>
    </div>
  );
}
