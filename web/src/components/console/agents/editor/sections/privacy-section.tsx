"use client";

/**
 * "Privacy" (V5-34, `docs/v5/PLAN-V5.md` V5-34): what to hide in transcripts,
 * what to keep once a call ends, whether analytics gets the full
 * conversation, and an optional cleanup model — `config.privacy`. Mounts the
 * post-call fields editor (`qa-section.tsx`) as a second card on the same
 * page; both edit the same agent config, so they save together.
 *
 * Plain wording throughout: "PII" appears only beside "personal details",
 * and the three storage tiers are named by what they do, not by their
 * `storage_tier` value.
 */
import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";

import { Checkbox } from "@/components/ui/checkbox";
import { Field } from "@/components/shared/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Section, SectionRow } from "@/components/shared/section";
import { Switch } from "@/components/ui/switch";
import { ProviderSlotCard } from "@/components/console/registry/provider-slot-card";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

import { QaFieldsCard } from "./qa-section";

type SttRedaction = NonNullable<
  AgentEditorForm["config"]["privacy"]
>["stt_redact"][number];

const REDACTION_OPTIONS: {
  value: SttRedaction;
  label: string;
  hint: string;
}[] = [
  {
    value: "pci",
    label: "Card numbers",
    hint: "Credit and debit card numbers.",
  },
  {
    value: "pii",
    label: "Personal details (PII)",
    hint: "Names, addresses and similar personal details.",
  },
  {
    value: "phi",
    label: "Health details",
    hint: "Medical conditions, treatments and similar.",
  },
  {
    value: "numbers",
    label: "Every number",
    hint: "Any spoken sequence of digits, not just the ones above.",
  },
];

type StorageTier = NonNullable<
  AgentEditorForm["config"]["privacy"]
>["storage_tier"];

const STORAGE_TIERS: { value: StorageTier; label: string; hint: string }[] = [
  {
    value: "full",
    label: "Keep everything",
    hint: "The full transcript and every tool call, as today.",
  },
  {
    value: "redacted",
    label: "Keep a cleaned copy",
    hint: "Card numbers, emails, phone numbers and long numbers are masked in the transcript and events.",
  },
  {
    value: "basic",
    label: "Keep the basics",
    hint: "The same cleanup as above, and tool call arguments and results are dropped too.",
  },
];

export function PrivacySection() {
  const { control } = useFormContext<AgentEditorForm>();
  const storageTier = useWatch({
    control,
    name: "config.privacy.storage_tier",
  });
  const [scrubExpanded, setScrubExpanded] = React.useState(false);

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="privacy-transcripts"
        title="What to hide in transcripts"
        description="Masked by the speech-to-text provider as it transcribes; only providers that support it apply it, and this agent's config is warned about the rest."
      >
        <SectionRow>
          <fieldset className="flex flex-col gap-2.5">
            <legend className="sr-only">What to hide in transcripts</legend>
            <Controller
              control={control}
              name="config.privacy.stt_redact"
              render={({ field }) => (
                <>
                  {REDACTION_OPTIONS.map((option) => {
                    const checked = (field.value ?? []).includes(option.value);
                    const id = `privacy-redact-${option.value}`;
                    return (
                      <label
                        key={option.value}
                        htmlFor={id}
                        className="flex items-start gap-2.5 text-sm"
                      >
                        <Checkbox
                          id={id}
                          className="mt-0.5"
                          checked={checked}
                          onCheckedChange={(next) => {
                            const value = field.value ?? [];
                            field.onChange(
                              next === true
                                ? [...value, option.value]
                                : value.filter((v) => v !== option.value),
                            );
                          }}
                        />
                        <span className="flex flex-col gap-0.5">
                          <span className="font-medium text-foreground">
                            {option.label}
                          </span>
                          <span className="text-[0.8125rem] text-muted-foreground">
                            {option.hint}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </>
              )}
            />
          </fieldset>
        </SectionRow>
      </Section>

      <Section
        id="privacy-storage"
        title="What to keep"
        description="What's kept in this agent's sessions once a call ends."
      >
        <SectionRow>
          <Controller
            control={control}
            name="config.privacy.storage_tier"
            render={({ field }) => (
              <RadioGroup
                value={field.value ?? "full"}
                onValueChange={field.onChange}
                aria-label="What to keep"
                data-issue-path="privacy.storage_tier"
                className="flex flex-col gap-2.5"
              >
                {STORAGE_TIERS.map((tier) => {
                  const id = `privacy-tier-${tier.value}`;
                  return (
                    <label
                      key={tier.value}
                      htmlFor={id}
                      className="flex items-start gap-2.5 rounded-md border border-border p-3 text-sm has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft/40"
                    >
                      <RadioGroupItem
                        id={id}
                        value={tier.value}
                        className="mt-0.5"
                      />
                      <span className="flex flex-col gap-0.5">
                        <span className="font-medium text-foreground">
                          {tier.label}
                        </span>
                        <span className="text-[0.8125rem] text-pretty text-muted-foreground">
                          {tier.hint}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </RadioGroup>
            )}
          />
        </SectionRow>
        {storageTier && storageTier !== "full" ? (
          <SectionRow>
            <Controller
              control={control}
              name="config.privacy.scrub_model"
              render={({ field }) => (
                <ProviderSlotCard
                  title="Cleanup model"
                  description="An LLM that also masks names, addresses and other personal details after the call. Only OpenAI or OpenRouter can run this from the server today — others are skipped with a warning."
                  kind="llm"
                  constraints={{ inference: "off" }}
                  value={field.value ?? null}
                  onChange={field.onChange}
                  expanded={scrubExpanded}
                  onExpandedChange={setScrubExpanded}
                  onRemove={
                    field.value ? () => field.onChange(null) : undefined
                  }
                  issuePath="privacy.scrub_model"
                />
              )}
            />
          </SectionRow>
        ) : null}
      </Section>

      <Section
        id="privacy-analytics"
        title="Analytics"
        description="Whether a third-party analytics tool your workspace has connected receives the full conversation."
      >
        <SectionRow>
          <Field
            inline
            label="Send full detail to analytics"
            htmlFor="privacy-telemetry"
            hint="This doesn't change what LiveKit Cloud's own dashboard shows — that's set in the LiveKit Cloud project, separately."
          >
            <Controller
              control={control}
              name="config.privacy.telemetry_pii"
              render={({ field }) => (
                <Switch
                  id="privacy-telemetry"
                  checked={field.value ?? true}
                  onCheckedChange={field.onChange}
                />
              )}
            />
          </Field>
        </SectionRow>
      </Section>

      <QaFieldsCard />
    </div>
  );
}
