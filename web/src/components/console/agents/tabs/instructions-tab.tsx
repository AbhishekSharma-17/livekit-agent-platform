"use client";

import * as React from "react";
import Link from "next/link";
import { Controller, useFormContext } from "react-hook-form";

import { Field } from "@/components/shared/field";
import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { usePacks } from "@/components/console/lib/api-hooks";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "hi", label: "Hindi" },
  { value: "ja", label: "Japanese" },
];

/** `Intl.supportedValuesOf` isn't in every runtime yet; a short curated fallback keeps the field usable. */
const FALLBACK_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Asia/Kolkata",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Australia/Sydney",
];

function supportedTimezones(): string[] {
  try {
    const withSupportedValuesOf = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
    const values = withSupportedValuesOf.supportedValuesOf?.("timeZone");
    if (values && values.length > 0) return values;
  } catch {
    // fall through to the curated list
  }
  return FALLBACK_TIMEZONES;
}

/**
 * "Instructions & voice" (docs/UI_UX_SPEC.md §4.6, §7.6). Unchanged by the
 * v2 amendments. Copy table (old → new) from §4.6:
 * "Greeting mode" → "How to greet"; "Say (TTS reads it verbatim)" → "Say it
 * exactly"; "Generate (model paraphrases it)" → "Let the model paraphrase";
 * "User-away timeout (seconds)" → "End the call when the caller is silent
 * for … seconds"; "Allow interruptions" → "Let callers interrupt".
 */
export function InstructionsTab({ agent }: { agent: AgentOut }) {
  const timezoneListId = React.useId();
  const { register, control, watch, setValue, formState } = useFormContext<AgentEditorForm>();
  const instructions = watch("config.instructions");
  const mode = watch("config.pipeline.mode");
  const instructionsError = formState.errors.config?.instructions?.message;

  const packsQuery = usePacks();
  const pack = packsQuery.data?.items.find((p) => p.manifest.id === agent.pack_id)?.manifest;
  const hasModeInstructions = Boolean(pack?.instructions_by_mode && Object.keys(pack.instructions_by_mode).length > 0);

  const timezones = React.useMemo(() => supportedTimezones(), []);
  const charCount = instructions?.length ?? 0;
  const tokenEstimate = Math.max(0, Math.round(charCount / 4));

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="instructions-section"
        title="Instructions"
        description="Tell the agent who it is, how to speak and what it must never promise. Tools are described automatically."
      >
        <SectionRow className="flex flex-col gap-2">
          <Field
            label="Instructions"
            htmlFor="instructions"
            required
            error={instructionsError}
          >
            <Textarea
              id="instructions"
              className="max-h-[60vh] min-h-48 overflow-y-auto text-[0.9375rem] leading-[1.6]"
              placeholder="You are a helpful assistant for..."
              aria-invalid={instructionsError ? true : undefined}
              data-issue-path="instructions"
              {...register("config.instructions")}
            />
          </Field>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[0.8125rem] tabular-nums text-muted-foreground">
              {charCount} characters · ~{tokenEstimate} tokens
            </p>
            {pack?.default_instructions ? (
              <ConfirmDialog
                trigger={
                  <Button type="button" variant="outline" size="sm">
                    Reset to pack default
                  </Button>
                }
                title="Reset instructions to the pack default?"
                description={`This replaces your current instructions with the "${pack.name}" pack's default: "${pack.default_instructions.slice(0, 160)}${pack.default_instructions.length > 160 ? "…" : ""}"`}
                confirmLabel="Reset"
                destructive={false}
                onConfirm={() => setValue("config.instructions", pack.default_instructions, { shouldDirty: true })}
              />
            ) : null}
          </div>
          {hasModeInstructions ? (
            <p className="text-[0.8125rem] text-muted-foreground">
              This pack has mode-specific instructions; the realtime variant is applied automatically.
            </p>
          ) : null}
        </SectionRow>
      </Section>

      <Section id="voice" title="Voice" description="How the agent greets callers and whether they can interrupt it.">
        <SectionRow>
          <Field label="Greeting" htmlFor="greeting" hint="The first thing the agent says.">
            <Textarea id="greeting" rows={2} {...register("config.voice.greeting")} />
          </Field>
        </SectionRow>

        <SectionRow className="flex flex-col gap-2">
          <Controller
            control={control}
            name="config.voice.greeting_mode"
            render={({ field }) => (
              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium leading-5">How to greet</span>
                <RadioGroup value={field.value} onValueChange={field.onChange} aria-label="How to greet" className="gap-2.5">
                  <label htmlFor="greeting-mode-say" className="flex items-center gap-2 text-sm">
                    <RadioGroupItem id="greeting-mode-say" value="say" /> Say it exactly
                  </label>
                  <label htmlFor="greeting-mode-generate" className="flex items-center gap-2 text-sm">
                    <RadioGroupItem id="greeting-mode-generate" value="generate" /> Let the model paraphrase
                  </label>
                </RadioGroup>
              </div>
            )}
          />
          {mode === "realtime" || mode === "half_cascade" ? (
            <p className="text-[0.8125rem] text-muted-foreground">
              For a realtime model with no separate voice, the greeting is spoken by the realtime model itself.
            </p>
          ) : null}
        </SectionRow>

        <SectionRow className="grid gap-5 sm:grid-cols-2">
          <Controller
            control={control}
            name="config.voice.language"
            render={({ field }) => (
              <Field label="Language" htmlFor="voice-language">
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="voice-language" className="w-full" data-issue-path="voice.language">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {LANGUAGES.map((l) => (
                      <SelectItem key={l.value} value={l.value}>
                        {l.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          />

          <Field
            label="Business timezone"
            htmlFor="agent-timezone"
            hint="Used for opening hours and bookings."
          >
            <Input
              id="agent-timezone"
              list={timezoneListId}
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-[0.8125rem]"
              placeholder="UTC"
              data-issue-path="timezone"
              {...register("config.timezone")}
            />
            <datalist id={timezoneListId}>
              {timezones.map((tz) => (
                <option key={tz} value={tz} />
              ))}
            </datalist>
          </Field>
        </SectionRow>

        <SectionRow className="flex flex-col gap-2">
          <Controller
            control={control}
            name="config.locale.caller_timezone"
            render={({ field }) => (
              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium leading-5">Caller&apos;s time</span>
                <RadioGroup
                  value={field.value ?? "detect"}
                  onValueChange={field.onChange}
                  aria-label="Caller's time"
                  className="gap-2.5"
                  data-issue-path="locale.caller_timezone"
                >
                  <label htmlFor="caller-timezone-detect" className="flex items-center gap-2 text-sm">
                    <RadioGroupItem id="caller-timezone-detect" value="detect" /> Use the caller&apos;s own timezone
                    (detected)
                  </label>
                  <label htmlFor="caller-timezone-business" className="flex items-center gap-2 text-sm">
                    <RadioGroupItem id="caller-timezone-business" value="business" /> Always use the business timezone
                  </label>
                </RadioGroup>
              </div>
            )}
          />
        </SectionRow>

        <SectionRow>
          <Controller
            control={control}
            name="config.voice.user_away_timeout_s"
            render={({ field }) => (
              <Field
                label="End the call when the caller is silent for"
                htmlFor="user-away-timeout"
                optional
                hint="Leave empty to never end the call for silence."
              >
                <InputGroup className="max-w-40">
                  <InputGroupInput
                    id="user-away-timeout"
                    type="number"
                    inputMode="numeric"
                    min={1}
                    value={field.value ?? ""}
                    onChange={(event) =>
                      field.onChange(event.target.value === "" ? null : Number(event.target.value))
                    }
                    data-issue-path="voice.user_away_timeout_s"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText>seconds</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              </Field>
            )}
          />
        </SectionRow>

        <SectionRow>
          <Field
            inline
            label="Let callers interrupt"
            htmlFor="allow-interruptions"
            hint="Turn off for read-aloud agents."
          >
            <Controller
              control={control}
              name="config.voice.allow_interruptions"
              render={({ field }) => (
                <Switch
                  id="allow-interruptions"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  data-issue-path="voice.allow_interruptions"
                />
              )}
            />
          </Field>
        </SectionRow>
      </Section>

      {/*
       * V5-11: the old "Conversation" card (read tools run, thinking sound,
       * plus presets/turn-taking/turn detector/background sound/noise
       * cancellation it never showed) moved to its own Conversation section;
       * this tab keeps a link there instead of duplicating it.
       */}
      <p className="text-[0.8125rem] text-muted-foreground">
        Turn taking, presets, sounds and noise cancellation moved to{" "}
        <Link href={`/console/agents/${agent.id}?section=conversation`} className="underline underline-offset-2">
          Conversation
        </Link>
        .
      </p>
    </div>
  );
}
