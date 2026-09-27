"use client";

/**
 * "Memory" (V5-42, `docs/v5/PLAN-V5.md` V5-42; ask #263): whether the agent
 * remembers a returning caller between calls — `config.memory`. Off by
 * default (`MemoryConfig.enabled = False`); every other field is disabled
 * until it's turned on, but stays visible so the setting isn't hidden.
 *
 * Plain wording throughout, per the §0.1 UI rule: never "Mem0", "subject
 * id" or "HMAC" — always "pseudonymous id". The explanation paragraph
 * (always shown, on or off) states what most needs saying up front: callers
 * are identified by a scrambled id rather than by name or number, a phone
 * number itself is never stored, an anonymous web visitor (nobody the
 * platform can recognize again) is never remembered, and a builder can
 * forget one caller or every caller any time (the session detail's Memory
 * tab and Settings → Danger zone).
 */
import { Controller, useFormContext, useWatch } from "react-hook-form";

import { Field } from "@/components/shared/field";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Section, SectionRow } from "@/components/shared/section";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

import { describedBy } from "./field-aria";

type MemoryScope = NonNullable<AgentEditorForm["config"]["memory"]>["scope"];

const SCOPE_OPTIONS: { value: MemoryScope; label: string; hint: string }[] = [
  {
    value: "agent",
    label: "Only this agent",
    hint: "What this agent remembers about a caller stays with this agent.",
  },
  {
    value: "workspace",
    label: "All agents in this workspace",
    hint: "Shared with every other agent whose Memory card also chooses this option.",
  },
];

export function MemorySection() {
  const { control, formState } = useFormContext<AgentEditorForm>();
  const enabled = useWatch({ control, name: "config.memory.enabled" });
  const retentionError = formState.errors.config?.memory?.retention_days?.message;
  const consentError = formState.errors.config?.memory?.consent_line?.message;
  const recallError = formState.errors.config?.memory?.max_recall_tokens?.message;

  return (
    <Section
      id="memory-settings"
      title="Memory"
      description="Remember what a returning caller told a previous agent, so the next call doesn't start from zero."
    >
      <SectionRow>
        <p className="max-w-[70ch] text-[0.8125rem] text-pretty text-muted-foreground">
          Callers are identified by a pseudonymous id — a scrambled code, never their name or phone
          number. An anonymous web visitor (someone the platform can&apos;t recognize on a later visit) is
          never remembered. A builder can forget one caller, or every caller&apos;s memories at once, at any
          time (the caller&apos;s own session page, or Settings → Danger zone).
        </p>
      </SectionRow>
      <SectionRow>
        <Field
          inline
          label="Remember returning callers"
          htmlFor="memory-enabled"
          hint="Nothing is read or written about a caller while this is off."
        >
          <Controller
            control={control}
            name="config.memory.enabled"
            render={({ field }) => (
              <Switch
                id="memory-enabled"
                checked={field.value}
                onCheckedChange={field.onChange}
                aria-describedby={describedBy("memory-enabled")}
                data-issue-path="memory.enabled"
              />
            )}
          />
        </Field>
      </SectionRow>
      <SectionRow>
        <fieldset className="flex flex-col gap-2.5">
          <legend className="text-sm font-medium text-foreground">Share memories with</legend>
          <Controller
            control={control}
            name="config.memory.scope"
            render={({ field }) => (
              <RadioGroup
                value={field.value ?? "agent"}
                onValueChange={field.onChange}
                aria-label="Share memories with"
                disabled={!enabled}
                data-issue-path="memory.scope"
                className="flex flex-col gap-2.5"
              >
                {SCOPE_OPTIONS.map((option) => {
                  const id = `memory-scope-${option.value}`;
                  return (
                    <label
                      key={option.value}
                      htmlFor={id}
                      className="flex items-start gap-2.5 rounded-md border border-border p-3 text-sm has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft/40 has-[:disabled]:opacity-60"
                    >
                      <RadioGroupItem id={id} value={option.value} className="mt-0.5" />
                      <span className="flex flex-col gap-0.5">
                        <span className="font-medium text-foreground">{option.label}</span>
                        <span className="text-[0.8125rem] text-pretty text-muted-foreground">{option.hint}</span>
                      </span>
                    </label>
                  );
                })}
              </RadioGroup>
            )}
          />
        </fieldset>
      </SectionRow>
      <SectionRow>
        <Field
          label="Forget after"
          htmlFor="memory-retention"
          hint="Days since the caller's last call before their memories are deleted on their own."
          error={retentionError}
        >
          <Controller
            control={control}
            name="config.memory.retention_days"
            render={({ field }) => (
              <InputGroup className="max-w-48" aria-disabled={!enabled || undefined}>
                <InputGroupInput
                  id="memory-retention"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={3650}
                  disabled={!enabled}
                  value={typeof field.value === "number" && Number.isFinite(field.value) ? field.value : ""}
                  onBlur={field.onBlur}
                  onChange={(event) =>
                    field.onChange(event.target.value === "" ? Number.NaN : Number(event.target.value))
                  }
                  aria-invalid={retentionError ? true : undefined}
                  aria-describedby={describedBy("memory-retention", Boolean(retentionError))}
                  data-issue-path="memory.retention_days"
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText>days</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
            )}
          />
        </Field>
      </SectionRow>
      <SectionRow>
        <Field
          label="What the agent says about memory"
          htmlFor="memory-consent-line"
          optional
          hint="Said early in the call so the caller knows they'll be remembered. Leave empty to say nothing."
          error={consentError}
        >
          <Controller
            control={control}
            name="config.memory.consent_line"
            render={({ field }) => (
              <Textarea
                id="memory-consent-line"
                rows={2}
                maxLength={500}
                disabled={!enabled}
                value={field.value ?? ""}
                onChange={(event) => field.onChange(event.target.value === "" ? null : event.target.value)}
                aria-invalid={consentError ? true : undefined}
                aria-describedby={describedBy("memory-consent-line", Boolean(consentError))}
                data-issue-path="memory.consent_line"
              />
            )}
          />
        </Field>
      </SectionRow>
      <SectionRow>
        <Field
          label="How much to recall"
          htmlFor="memory-recall-tokens"
          hint="The most earlier detail brought into a call at once. Higher recalls more but costs a little more each call."
          error={recallError}
        >
          <Controller
            control={control}
            name="config.memory.max_recall_tokens"
            render={({ field }) => (
              <InputGroup className="max-w-48" aria-disabled={!enabled || undefined}>
                <InputGroupInput
                  id="memory-recall-tokens"
                  type="number"
                  inputMode="numeric"
                  min={50}
                  max={2000}
                  disabled={!enabled}
                  value={typeof field.value === "number" && Number.isFinite(field.value) ? field.value : ""}
                  onBlur={field.onBlur}
                  onChange={(event) =>
                    field.onChange(event.target.value === "" ? Number.NaN : Number(event.target.value))
                  }
                  aria-invalid={recallError ? true : undefined}
                  aria-describedby={describedBy("memory-recall-tokens", Boolean(recallError))}
                  data-issue-path="memory.max_recall_tokens"
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText>tokens</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
            )}
          />
        </Field>
      </SectionRow>
      <SectionRow>
        <Field
          inline
          label="Store what the caller said as it was (no model)"
          htmlFor="memory-verbatim"
          hint="Off: a language model picks out short facts after the call. On: the caller's own words are kept, unprocessed, with no model call."
        >
          <Controller
            control={control}
            name="config.memory.verbatim"
            render={({ field }) => (
              <Switch
                id="memory-verbatim"
                checked={field.value}
                disabled={!enabled}
                onCheckedChange={field.onChange}
                aria-describedby={describedBy("memory-verbatim")}
                data-issue-path="memory.verbatim"
              />
            )}
          />
        </Field>
      </SectionRow>
    </Section>
  );
}
