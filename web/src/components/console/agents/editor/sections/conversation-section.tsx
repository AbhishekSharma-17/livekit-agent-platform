"use client";

import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { ChevronRightIcon } from "lucide-react";

import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useProviders } from "@/components/console/lib/api-hooks";
import { useEditorContext } from "@/components/console/agents/editor/editor-context";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { cn } from "@/lib/utils";

import { describedBy } from "./field-aria";

/**
 * "Conversation" (V5-11, `docs/v5/PLAN-V5.md` V5-11, `docs/UI_UX_SPEC.md`
 * §4.6a): presets, turn taking, interruptions, the turn detector, sounds and
 * noise cancellation. Edits `config.pipeline.{turn_handling,
 * conversation_preset, turn_detector}` and `config.voice.{thinking_sound,
 * ambient_sound}`; `config.tools.execution_default` (the V4-13 "Read tools
 * run" picker) moved here too. Never edits `config.pipeline.noise_cancellation`
 * itself (still an "Advanced" provider slot in the Providers section) — this
 * section only shows what is selected and its price note.
 *
 * The preset radio cards give one-line, qualitative descriptions rather than
 * the pinned numeric values of `lkap_contracts.turn_handling.CONVERSATION_PRESETS`:
 * R-V5-4 requires reading that table "through the generated `.d.ts` or a
 * generated JSON, never a hand copy", and no such generated source exists yet
 * (`docs/v5/_asks.md` #76 asks for one). Choosing a preset only ever writes
 * `conversation_preset`; the stored `turn_handling` is left untouched
 * (`lkap_contracts.turn_handling.resolve_turn_handling` expands it at session
 * start). Editing any turn-taking field below flips the preset to Custom.
 */

type ConversationPreset = NonNullable<AgentEditorForm["config"]["pipeline"]["conversation_preset"]>;

const PRESETS: { value: ConversationPreset; label: string; description: string }[] = [
  { value: "patient", label: "Patient", description: "Waits longer before replying — good for callers who pause to think." },
  { value: "balanced", label: "Balanced", description: "A good default for most calls." },
  { value: "snappy", label: "Snappy", description: "Replies quickly, and can start before the caller finishes talking." },
  { value: "telephony", label: "Phone call", description: "Tuned for phone audio, where a short silence is easy to mishear." },
  { value: "custom", label: "Custom", description: "Set your own timing below." },
];

const THINKING_SOUNDS = [
  { value: "none", label: "None" },
  { value: "keyboard_typing", label: "Keyboard typing" },
  { value: "keyboard_typing2", label: "Keyboard typing (alternate)" },
  { value: "office_ambience", label: "Office ambience" },
] as const;

/** `lkap_contracts.turn_handling.AMBIENT_SOUNDS`, hand-listed like `THINKING_SOUNDS` above (labels only, not behavior — see the file header on why the preset table itself is not hand-copied). */
const AMBIENT_SOUNDS = [
  { value: "none", label: "None" },
  { value: "city_ambience", label: "City" },
  { value: "forest_ambience", label: "Forest" },
  { value: "office_ambience", label: "Office" },
  { value: "crowded_room", label: "Crowded room" },
  { value: "keyboard_typing", label: "Keyboard typing" },
  { value: "keyboard_typing2", label: "Keyboard typing (alternate)" },
  { value: "hold_music", label: "Hold music" },
] as const;

/** Below this, a chain of background announcements can exhaust the tool-steps budget (api's `MIN_TOOL_STEPS_FOR_BACKGROUND`, R-V4-35). */
const MIN_TOOL_STEPS_FOR_BACKGROUND = 4;

function numberOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

export function ConversationSection() {
  const { control, watch, setValue, getValues } = useFormContext<AgentEditorForm>();
  const ctx = useEditorContext();

  const preset = watch("config.pipeline.conversation_preset") ?? "custom";
  const executionDefault = watch("config.tools.execution_default");
  const maxToolSteps = watch("config.tools.max_tool_steps");
  const stepsWarning = executionDefault !== "blocking" && maxToolSteps < MIN_TOOL_STEPS_FOR_BACKGROUND;
  const noiseCancellation = watch("config.pipeline.noise_cancellation");

  const providersQuery = useProviders();
  const ncProvider = providersQuery.data?.providers.find((p) => p.id === noiseCancellation?.provider_id);

  /** Any structured turn-taking edit flips the preset to Custom (acceptance: presets stay untouched by non-preset edits). */
  function markCustom() {
    if (getValues("config.pipeline.conversation_preset") !== "custom") {
      setValue("config.pipeline.conversation_preset", "custom", { shouldDirty: true });
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Section
        id="conversation-presets"
        title="Conversation"
        description="How the agent paces a call: when it decides you're done talking, how easily it's interrupted, and what it sounds like while it works."
      >
        <SectionRow className="flex flex-col gap-3">
          <Controller
            control={control}
            name="config.pipeline.conversation_preset"
            render={({ field }) => (
              <RadioGroup
                value={field.value ?? "custom"}
                onValueChange={field.onChange}
                aria-label="Conversation preset"
                data-issue-path="pipeline.conversation_preset"
                className="grid gap-2.5 sm:grid-cols-2"
              >
                {PRESETS.map((p) => {
                  const id = `preset-${p.value}`;
                  const selected = (field.value ?? "custom") === p.value;
                  return (
                    <label
                      key={p.value}
                      htmlFor={id}
                      className={cn(
                        "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors duration-(--dur-2)",
                        selected ? "border-brand-line bg-brand-soft/40 ring-1 ring-brand-line" : "border-border hover:border-foreground/20",
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium leading-5">{p.label}</span>
                        <span className="mt-0.5 block text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">
                          {p.description}
                        </span>
                      </span>
                      <RadioGroupItem id={id} value={p.value} className="mt-0.5" />
                    </label>
                  );
                })}
              </RadioGroup>
            )}
          />
        </SectionRow>
      </Section>

      <Section
        id="turn-taking"
        title="Turn taking"
        description={
          preset === "custom"
            ? "Fine-tune how the agent takes turns. Changing any of these switches the preset above to Custom."
            : `Using the "${PRESETS.find((p) => p.value === preset)?.label ?? preset}" preset's own timing. Changing a field below switches to Custom.`
        }
      >
        <SectionRow className="grid gap-5 sm:grid-cols-2">
          <Controller
            control={control}
            name="config.pipeline.turn_handling.endpointing.min_delay"
            render={({ field }) => (
              <Field
                label="Wait at least"
                htmlFor="wait-min-delay"
                optional
                hint="Silence shorter than this never ends the caller's turn."
              >
                <InputGroup>
                  <InputGroupInput
                    id="wait-min-delay"
                    type="number"
                    inputMode="decimal"
                    step={0.1}
                    min={0}
                    value={field.value ?? ""}
                    onChange={(event) => {
                      field.onChange(numberOrNull(event.target.value));
                      markCustom();
                    }}
                    data-issue-path="pipeline.turn_handling.endpointing.min_delay"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText>seconds</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              </Field>
            )}
          />
          <Controller
            control={control}
            name="config.pipeline.turn_handling.endpointing.max_delay"
            render={({ field }) => (
              <Field
                label="Wait at most"
                htmlFor="wait-max-delay"
                optional
                hint="Longest the agent waits for a hesitant caller before replying anyway."
              >
                <InputGroup>
                  <InputGroupInput
                    id="wait-max-delay"
                    type="number"
                    inputMode="decimal"
                    step={0.1}
                    min={0}
                    value={field.value ?? ""}
                    onChange={(event) => {
                      field.onChange(numberOrNull(event.target.value));
                      markCustom();
                    }}
                    data-issue-path="pipeline.turn_handling.endpointing.max_delay"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText>seconds</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              </Field>
            )}
          />
        </SectionRow>

        <SectionRow className="grid gap-5 sm:grid-cols-2">
          <Controller
            control={control}
            name="config.pipeline.turn_handling.interruption.min_duration"
            render={({ field }) => (
              <Field
                label="Let the caller interrupt after"
                htmlFor="interrupt-min-duration"
                optional
                hint="Shorter values stop the agent talking sooner once the caller starts speaking."
              >
                <InputGroup>
                  <InputGroupInput
                    id="interrupt-min-duration"
                    type="number"
                    inputMode="decimal"
                    step={0.1}
                    min={0}
                    value={field.value ?? ""}
                    onChange={(event) => {
                      field.onChange(numberOrNull(event.target.value));
                      markCustom();
                    }}
                    data-issue-path="pipeline.turn_handling.interruption.min_duration"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText>seconds of speech</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              </Field>
            )}
          />
          <Controller
            control={control}
            name="config.pipeline.turn_handling.interruption.min_words"
            render={({ field }) => (
              <Field
                label="Minimum words to interrupt"
                htmlFor="interrupt-min-words"
                optional
                hint="Needs live speech-to-text. Leave blank for any speech to count."
              >
                <InputGroup>
                  <InputGroupInput
                    id="interrupt-min-words"
                    type="number"
                    inputMode="numeric"
                    step={1}
                    min={0}
                    value={field.value ?? ""}
                    onChange={(event) => {
                      const value = event.target.value;
                      field.onChange(value === "" ? null : Math.trunc(Number(value)));
                      markCustom();
                    }}
                    data-issue-path="pipeline.turn_handling.interruption.min_words"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText>words</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              </Field>
            )}
          />
        </SectionRow>

        <SectionRow>
          <Field
            inline
            label="Reply while the caller is still finishing"
            htmlFor="preemptive-generation"
            hint="Starts preparing the reply early — usually faster, but the draft is sometimes thrown away."
          >
            <Controller
              control={control}
              name="config.pipeline.turn_handling.preemptive_generation.enabled"
              render={({ field }) => (
                <Switch
                  id="preemptive-generation"
                  checked={field.value ?? false}
                  onCheckedChange={(checked) => {
                    field.onChange(checked);
                    markCustom();
                  }}
                  aria-describedby={describedBy("preemptive-generation")}
                  data-issue-path="pipeline.turn_handling.preemptive_generation.enabled"
                />
              )}
            />
          </Field>
        </SectionRow>
      </Section>

      <Section id="turn-detector" title="Turn detector" description="Where end-of-turn is decided and how sensitive it is.">
        <SectionRow className="flex flex-col gap-2">
          <Controller
            control={control}
            name="config.pipeline.turn_detector.mode"
            render={({ field }) => (
              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium leading-5">Where it runs</span>
                <RadioGroup
                  value={field.value ?? ""}
                  onValueChange={field.onChange}
                  aria-label="Where the turn detector runs"
                  className="gap-2.5"
                  data-issue-path="pipeline.turn_detector.mode"
                >
                  <label htmlFor="turn-detector-hosted" className="flex items-center gap-2 text-sm">
                    <RadioGroupItem id="turn-detector-hosted" value="hosted" /> Hosted
                  </label>
                  <label htmlFor="turn-detector-local" className="flex items-center gap-2 text-sm">
                    <RadioGroupItem id="turn-detector-local" value="local" /> On this worker
                  </label>
                </RadioGroup>
                <p className="text-[0.8125rem] leading-[1.125rem] text-muted-foreground">
                  Leave unset to let the connection decide.
                </p>
              </div>
            )}
          />
        </SectionRow>
        <SectionRow>
          <Controller
            control={control}
            name="config.pipeline.turn_detector.unlikely_threshold"
            render={({ field }) => (
              <Field
                label="Sensitivity"
                htmlFor="turn-detector-threshold"
                optional
                hint="Higher waits longer before assuming the caller has finished talking. Leave blank for the model's own default."
              >
                <InputGroup className="max-w-40">
                  <InputGroupInput
                    id="turn-detector-threshold"
                    type="number"
                    inputMode="decimal"
                    step={0.05}
                    min={0}
                    max={1}
                    value={field.value ?? ""}
                    onChange={(event) => field.onChange(numberOrNull(event.target.value))}
                    data-issue-path="pipeline.turn_detector.unlikely_threshold"
                  />
                </InputGroup>
              </Field>
            )}
          />
        </SectionRow>
      </Section>

      <Section id="conversation-sounds" title="Sounds" description="What the caller hears while the agent works, and in the background.">
        <SectionRow>
          <Controller
            control={control}
            name="config.voice.thinking_sound"
            render={({ field }) => (
              <Field
                label="Thinking sound"
                htmlFor="voice-thinking-sound"
                hint="Plays while the agent is working things out, e.g. on a blocking tool call."
              >
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="voice-thinking-sound" className="w-full sm:w-72" data-issue-path="voice.thinking_sound">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {THINKING_SOUNDS.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          />
        </SectionRow>
        <SectionRow>
          <Controller
            control={control}
            name="config.voice.ambient_sound"
            render={({ field }) => (
              <Field
                label="Background sound"
                htmlFor="voice-ambient-sound"
                hint="Plays quietly for the whole call. Needs audio output; not used in text chat."
              >
                <Select value={field.value ?? "none"} onValueChange={field.onChange}>
                  <SelectTrigger id="voice-ambient-sound" className="w-full sm:w-72" data-issue-path="voice.ambient_sound">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {AMBIENT_SOUNDS.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
          />
        </SectionRow>
        <SectionRow>
          {/*
           * Not a `Field` (its `htmlFor`/`<Label>` pairing needs a labelable
           * form control — an input/select/textarea/button — and this row is
           * read-only, so a plain heading avoids an invalid `label[for]`
           * association that axe and screen readers wouldn't resolve anyway.
           */}
          <div className="flex flex-col gap-1.5">
            <p className="text-sm font-medium leading-5">Noise cancellation</p>
            <p className="text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">
              Cleans up the caller&apos;s audio before it reaches speech-to-text. Chosen in the Providers section.
            </p>
            <div className="flex flex-col gap-1 text-sm">
              <span>{ncProvider ? ncProvider.label : "Off"}</span>
              {ncProvider?.price_note ? (
                <span className="text-[0.8125rem] text-muted-foreground">{ncProvider.price_note}</span>
              ) : null}
              {preset === "telephony" && ncProvider?.telephony_variant ? (
                <span className="text-[0.8125rem] text-muted-foreground">
                  On phone calls this automatically uses the variant tuned for phone audio.
                </span>
              ) : null}
              <Button
                type="button"
                variant="link"
                size="sm"
                className="h-auto w-fit p-0 text-[0.8125rem]"
                onClick={() => ctx?.goToSection("providers")}
              >
                Change in Providers
              </Button>
            </div>
          </div>
        </SectionRow>
      </Section>

      <Section
        id="conversation-tools"
        title="While a tool runs"
        description="How the agent behaves during background work."
      >
        <SectionRow>
          <Controller
            control={control}
            name="config.tools.execution_default"
            render={({ field }) => (
              <Field
                label="Read tools run"
                htmlFor="tools-execution-default"
                hint="Applies to GET web requests, search knowledge and describe current frame — anything that changes something always waits for the agent to reply."
              >
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="tools-execution-default" className="w-full sm:w-72" data-issue-path="tools.execution_default">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="blocking">Blocking — wait for the result</SelectItem>
                    <SelectItem value="auto">Automatic — background only if slow</SelectItem>
                    <SelectItem value="background">In the background — always</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            )}
          />
          {stepsWarning ? (
            // A server-issued `tools.max_tool_steps` issue routes to the Tools
            // section (`builtin-sections.tsx`'s `issuePaths`), whose own field
            // carries the same `data-issue-path` and is what actually receives
            // focus; `tabIndex` here only makes this local hint focusable too,
            // in case it is ever reached directly.
            <p data-issue-path="tools.max_tool_steps" tabIndex={-1} className="text-[0.8125rem] text-warning-text">
              Read tools run &quot;{executionDefault}&quot; and each announcement spends a tool step; with {maxToolSteps} tool
              steps a chain of lookups can run out of steps — use {MIN_TOOL_STEPS_FOR_BACKGROUND} or more (Tools tab →
              Advanced → Tool steps per turn).
            </p>
          ) : null}
        </SectionRow>
      </Section>

      <AdvancedTurnHandling />
    </div>
  );
}

/** The raw `turn_handling` JSON, for keys the form above doesn't show (`user_turn_limit`, `endpointing.mode`/`alpha`, …). */
function AdvancedTurnHandling() {
  const { control, setValue, getValues } = useFormContext<AgentEditorForm>();
  const turnHandling = useWatch({ control, name: "config.pipeline.turn_handling" });
  const [open, setOpen] = React.useState(false);
  const [focused, setFocused] = React.useState(false);
  const [draft, setDraft] = React.useState(() => JSON.stringify(turnHandling ?? {}, null, 2));
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    // Never overwrite the textarea while the field has an error: `commit` below
    // sets `focused` back to `false` on blur even when the JSON was invalid, and
    // without the `!error` guard this effect would immediately replace the
    // caller's un-parseable text with the last-good value the moment the error
    // appears — showing "Must be valid JSON" underneath text that no longer
    // matches what's actually stored.
    if (!focused && !error) setDraft(JSON.stringify(turnHandling ?? {}, null, 2));
  }, [turnHandling, focused, error]);

  function commit(value: string) {
    setFocused(false);
    const trimmed = value.trim();
    try {
      const parsed: unknown = JSON.parse(trimmed === "" ? "{}" : trimmed);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setError("Must be a JSON object");
        return;
      }
      setError(null);
      setValue("config.pipeline.turn_handling", parsed as AgentEditorForm["config"]["pipeline"]["turn_handling"], {
        shouldDirty: true,
      });
      if (getValues("config.pipeline.conversation_preset") !== "custom") {
        setValue("config.pipeline.conversation_preset", "custom", { shouldDirty: true });
      }
    } catch {
      setError("Must be valid JSON");
    }
  }

  return (
    <Section id="conversation-advanced" title="Advanced" description="For turn-handling keys the form above doesn't show yet.">
      <SectionRow>
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger
            className={cn(
              "group/more inline-flex items-center gap-1 rounded-xs text-[0.8125rem] font-medium text-muted-foreground outline-none",
              "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <Icon
              as={ChevronRightIcon}
              size="sm"
              className="transition-transform duration-(--dur-2) group-data-[state=open]/more:rotate-90"
            />
            Advanced
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-3">
            <Field
              label="Turn handling (JSON)"
              htmlFor="turn-handling-json"
              optional
              error={error}
              hint="Keys this form doesn't recognize are kept as-is; editing this also switches the preset above to Custom."
            >
              <Textarea
                id="turn-handling-json"
                className="min-h-40 font-mono text-xs"
                value={draft}
                onFocus={() => setFocused(true)}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={(event) => commit(event.target.value)}
                aria-invalid={error ? true : undefined}
                data-issue-path="pipeline.turn_handling"
              />
            </Field>
          </CollapsibleContent>
        </Collapsible>
      </SectionRow>
    </Section>
  );
}
