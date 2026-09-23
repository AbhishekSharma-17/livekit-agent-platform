"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";
import { AudioWaveformIcon, EyeOffIcon, PlusIcon, type LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Icon } from "@/components/shared/icon";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import { useProviders } from "@/components/console/lib/api-hooks";
import { ProviderSlotCard } from "@/components/console/registry/provider-slot-card";
import { isKnownTextOnlyLlm, type ProviderKind } from "@/components/console/registry/provider-meta";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { cn } from "@/lib/utils";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { ProviderSpec } from "@/contracts/lkap-contracts";
import { LoadingRegion } from "@/components/shared/loading-state";

type SlotKey = "stt" | "llm" | "tts" | "realtime" | "avatar" | "image_gen" | "workflow_llm";
type PipelineMode = AgentEditorForm["config"]["pipeline"]["mode"];

interface SlotDef {
  key: SlotKey;
  kind: ProviderKind;
  title: string;
  description: string;
}

const SLOTS: Record<SlotKey, SlotDef> = {
  stt: { key: "stt", kind: "stt", title: "Speech-to-text", description: "Turns the caller's speech into text." },
  llm: { key: "llm", kind: "llm", title: "Language model", description: "Decides what to say and which tools to call." },
  tts: { key: "tts", kind: "tts", title: "Text-to-speech", description: "Speaks the model's replies." },
  realtime: {
    key: "realtime",
    kind: "realtime",
    title: "Realtime model",
    description: "Hears, thinks and speaks in one model; can watch the camera live when it supports video.",
  },
  avatar: { key: "avatar", kind: "avatar", title: "Avatar", description: "A talking-head video shown on the call's stage." },
  image_gen: {
    key: "image_gen",
    kind: "image_gen",
    title: "Image generation",
    description: "Used by packs that draw sketches or illustrations, such as the insurance notebook.",
  },
  workflow_llm: {
    key: "workflow_llm",
    kind: "llm",
    title: "Workflow model",
    description:
      "Used by packs for structured extraction. When unset: the main language model (cascaded) or a LiveKit Inference model (realtime).",
  },
};

const PIPELINE_SLOTS: Record<PipelineMode, SlotKey[]> = {
  cascaded: ["stt", "llm", "tts"],
  realtime: ["realtime"],
  half_cascade: ["realtime", "tts"],
};

const OPTIONAL_SLOTS: { key: SlotKey; add: string }[] = [
  { key: "avatar", add: "Add avatar" },
  { key: "image_gen", add: "Add image generation" },
  { key: "workflow_llm", add: "Advanced: workflow model" },
];

/**
 * Providers section (docs/UI_UX_SPEC.md §4.4, §7.5 item 1): the pipeline as
 * a flow — mode as two descriptive cards, slot cards in pipeline order with
 * a connector between them (one expanded at a time), optional slots behind
 * "Add" buttons, and the vision coherence note on the language model.
 *
 * Each slot is a `ProviderSlotCard` around the composable
 * `ProviderSlotEditor` (`kind`, `value`, `onChange`, `constraints`); payloads
 * stay `ProviderRef` (contract unchanged). Switching mode keeps the other
 * mode's slots in form state; the save payload nulls them (WP-3's
 * `form-values.ts`).
 */
export function ProvidersTab() {
  const { data, isLoading, isError, error, refetch } = useProviders();
  const { control, watch, formState } = useFormContext<AgentEditorForm>();
  const mode = watch("config.pipeline.mode");
  const pipeline = watch("config.pipeline");
  const camera = watch("config.capabilities.camera");
  const screenShare = watch("config.capabilities.screen_share");
  const { issueFor } = useSectionIssues();

  const [expanded, setExpanded] = React.useState<SlotKey | null>(null);
  const [added, setAdded] = React.useState<Set<SlotKey>>(() => new Set());
  const [visionPickerOpen, setVisionPickerOpen] = React.useState(false);
  // Set by the vision note's link: the next open of the LLM model list is filtered to vision models.
  const [visionRequested, setVisionRequested] = React.useState(false);

  if (isLoading) {
    return <ProvidersSkeleton />;
  }

  if (isError) {
    return <ErrorBanner message={`Couldn't load the provider list — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  const providers: ProviderSpec[] = data?.providers ?? [];
  const pipelineErrors = formState.errors.config?.pipeline;
  const showVisionNote =
    mode === "cascaded" && (camera || screenShare) && isKnownTextOnlyLlm(providers, pipeline?.llm ?? null);

  function slotError(key: SlotKey): { message: string; tone: "error" | "warning" } | undefined {
    const zodMessage = (pipelineErrors?.[key] as { message?: string } | undefined)?.message;
    if (zodMessage) return { message: zodMessage, tone: "error" };
    const issue = issueFor(`pipeline.${key}`);
    if (issue) return { message: issue.message, tone: issue.severity === "error" ? "error" : "warning" };
    return undefined;
  }

  function toggle(key: SlotKey, open: boolean) {
    setExpanded(open ? key : null);
    if (!open && key === "llm") {
      setVisionPickerOpen(false);
      setVisionRequested(false);
    }
  }

  function renderSlot(key: SlotKey, optional: boolean) {
    const def = SLOTS[key];
    const problem = slotError(key);
    const isLlmWithNote = key === "llm" && showVisionNote;
    return (
      <Controller
        key={key}
        control={control}
        name={`config.pipeline.${key}`}
        render={({ field }) => (
          <ProviderSlotCard
            title={def.title}
            description={def.description}
            kind={def.kind}
            value={field.value}
            onChange={field.onChange}
            constraints={{ required: !optional, preferVision: isLlmWithNote && visionRequested }}
            providers={providers}
            idPrefix={`slot-${key}`}
            issuePath={`pipeline.${key}`}
            expanded={expanded === key}
            onExpandedChange={(open) => toggle(key, open)}
            onRemove={
              optional
                ? () => {
                    field.onChange(null);
                    setAdded((prev) => {
                      const next = new Set(prev);
                      next.delete(key);
                      return next;
                    });
                    if (expanded === key) setExpanded(null);
                  }
                : undefined
            }
            error={problem?.message}
            errorTone={problem?.tone}
            modelPickerOpen={key === "llm" ? visionPickerOpen : undefined}
            onModelPickerOpenChange={
              key === "llm"
                ? (open) => {
                    setVisionPickerOpen(open);
                    if (!open) setVisionRequested(false);
                  }
                : undefined
            }
            notice={
              isLlmWithNote ? (
                <VisionNote
                  onShowVisionModels={() => {
                    setExpanded("llm");
                    setVisionRequested(true);
                    setVisionPickerOpen(true);
                  }}
                />
              ) : undefined
            }
          />
        )}
      />
    );
  }

  const pipelineSlots = PIPELINE_SLOTS[mode] ?? PIPELINE_SLOTS.cascaded;
  const optionalShown = OPTIONAL_SLOTS.filter(({ key }) => Boolean(pipeline?.[key]) || added.has(key));
  const optionalHidden = OPTIONAL_SLOTS.filter(({ key }) => !pipeline?.[key] && !added.has(key));

  return (
    <div className="flex flex-col gap-8">
      <Group title="How it talks" description="Pick how speech flows through the agent. You can switch later; the other mode's choices are kept until you save.">
        <Controller
          control={control}
          name="config.pipeline.mode"
          render={({ field }) => <ModeCards value={field.value} onChange={field.onChange} />}
        />
      </Group>

      <Group title="Pipeline" description={pipelineSlots.length > 1 ? "In the order a turn flows through them." : undefined}>
        <ol className="flex flex-col" aria-label="Pipeline slots">
          {pipelineSlots.map((key, index) => (
            <li key={key} className="flex flex-col">
              {index > 0 ? <span aria-hidden="true" className="ml-7 h-4 w-px bg-border" /> : null}
              {renderSlot(key, false)}
            </li>
          ))}
        </ol>
      </Group>

      <Group title="Optional" description="Extras some packs use. Nothing here is needed for a call.">
        {optionalShown.length > 0 ? (
          <div className="flex flex-col gap-3">{optionalShown.map(({ key }) => renderSlot(key, true))}</div>
        ) : null}
        {optionalHidden.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {optionalHidden.map(({ key, add }) => (
              <Button
                key={key}
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  setAdded((prev) => new Set(prev).add(key));
                  setExpanded(key);
                }}
              >
                <Icon as={PlusIcon} size="sm" />
                {add}
              </Button>
            ))}
          </div>
        ) : null}
      </Group>
    </div>
  );
}

function Group({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  const id = React.useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 id={id} className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-balance text-foreground">
          {title}
        </h2>
        {description ? <p className="max-w-[65ch] text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

const MODES: { value: Exclude<PipelineMode, "half_cascade">; title: string; description: string; icon: LucideIcon | "dots" }[] = [
  {
    value: "cascaded",
    title: "Cascaded",
    description: "Separate speech-to-text, language model and text-to-speech. Most flexible; works with LiveKit Inference without keys.",
    icon: "dots",
  },
  {
    value: "realtime",
    title: "Realtime",
    description: "One speech-to-speech model (Gemini Live, GPT Realtime). Lowest latency; can watch the camera live.",
    icon: AudioWaveformIcon,
  },
];

function ModeCards({ value, onChange }: { value: PipelineMode; onChange: (next: PipelineMode) => void }) {
  const name = React.useId();
  return (
    <div className="flex flex-col gap-2">
      <div role="radiogroup" aria-label="Pipeline mode" className="grid gap-2 sm:grid-cols-2">
        {MODES.map((mode) => (
          <label
            key={mode.value}
            className={cn(
              "relative flex cursor-pointer gap-3 rounded-lg border border-border bg-card p-4",
              "transition-colors duration-(--dur-2) hover:bg-accent",
              "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
              "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-background",
            )}
          >
            <input
              type="radio"
              name={name}
              value={mode.value}
              checked={value === mode.value}
              onChange={() => onChange(mode.value)}
              className="sr-only"
            />
            <span
              aria-hidden="true"
              className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"
            >
              {mode.icon === "dots" ? <LinkedDots /> : <Icon as={mode.icon} size="md" />}
            </span>
            <span className="flex min-w-0 flex-col gap-1">
              <span className="text-sm font-semibold text-foreground">{mode.title}</span>
              <span className="text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">{mode.description}</span>
            </span>
          </label>
        ))}
      </div>
      {value === "half_cascade" ? (
        <p className="rounded-md bg-info-soft px-3 py-2 text-[0.8125rem] text-info-text">
          This agent uses half-cascade: a realtime model thinks, a separate voice speaks. Pick a card above to switch.
        </p>
      ) : null}
    </div>
  );
}

/** "Three linked dots" (§4.4) — no Lucide glyph says cascade. */
function LinkedDots() {
  return (
    <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
      <circle cx="3" cy="8" r="1.75" />
      <circle cx="8" cy="8" r="1.75" />
      <circle cx="13" cy="8" r="1.75" />
      <path d="M4.75 8h1.5M9.75 8h1.5" />
    </svg>
  );
}

function VisionNote({ onShowVisionModels }: { onShowVisionModels: () => void }) {
  return (
    <div className="flex items-start gap-2 rounded-md bg-warning-soft px-3 py-2 text-[0.8125rem] leading-[1.125rem] text-warning-text">
      <Icon as={EyeOffIcon} size="sm" className="mt-0.5" />
      <p className="text-pretty">
        This model can&apos;t see images; pick one marked Vision to use the camera.{" "}
        <button
          type="button"
          onClick={onShowVisionModels}
          className="rounded-xs font-medium underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Show vision models
        </button>
      </p>
    </div>
  );
}

function ProvidersSkeleton() {
  return (
    <LoadingRegion label="Loading providers" className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-32" />
        <div className="grid gap-2 sm:grid-cols-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-24" />
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-28 w-full" />
        ))}
      </div>
    </LoadingRegion>
  );
}
