"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";
import {
  AudioWaveformIcon,
  ChevronRightIcon,
  EyeOffIcon,
  MessagesSquareIcon,
  PlusIcon,
  TriangleAlertIcon,
  type LucideIcon,
  UserRoundIcon,
  WaypointsIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { resolveBoundConnection } from "@/components/console/agents/providers-section/connection-gate";
import {
  avatarConnectionNote,
  noiseConnectionNote,
  turnEnderSentence,
  whoEndsTurn,
} from "@/components/console/agents/providers-section/pipeline-summary";
import { ConnectionNotes, PipelineSummaryLine } from "@/components/console/agents/providers-section/pipeline-summary-line";
import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import { useProviders } from "@/components/console/lib/api-hooks";
import { ProviderSlotCard } from "@/components/console/registry/provider-slot-card";
import { PART_JOBS } from "@/components/console/registry/slot-jobs";
import {
  connectionDisabledReason,
  isKnownTextOnlyLlm,
  slotAvailability,
  type ProviderKind,
} from "@/components/console/registry/provider-meta";
import type { SlotConstraints } from "@/components/console/registry/provider-slot-editor";
import { useWriteAccess } from "@/components/console/lib/roles";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useConnections } from "@/hooks/useConnections";
import { cn } from "@/lib/utils";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { EditorSectionProps } from "@/components/console/agents/editor/types";
import type { FieldSpec, ProviderOut, ProviderSpec } from "@/contracts/lkap-contracts";
import { LoadingRegion } from "@/components/shared/loading-state";
import {
  aspectRatioStyle,
  resolveFrame,
  type AvatarFit,
  type AvatarFraming,
} from "@/components/session/avatar-framing";

type SlotKey = "stt" | "llm" | "tts" | "realtime" | "avatar" | "image_gen" | "workflow_llm" | "vad" | "turn_detection" | "noise_cancellation";
type PipelineMode = AgentEditorForm["config"]["pipeline"]["mode"];

interface SlotDef {
  key: SlotKey;
  kind: ProviderKind;
  /** The technical name, shown small under the job (V6-33). */
  title: string;
  /** What the part does, in plain words; leads the card. */
  job: string;
  /** A line under the job where there is more to say; the turn-detection one is built from the agent's setup. */
  description?: string;
}

const SLOTS: Record<SlotKey, SlotDef> = {
  stt: { key: "stt", kind: "stt", title: PART_JOBS.stt.name, job: PART_JOBS.stt.job },
  llm: { key: "llm", kind: "llm", title: PART_JOBS.llm.name, job: PART_JOBS.llm.job },
  tts: { key: "tts", kind: "tts", title: PART_JOBS.tts.name, job: PART_JOBS.tts.job },
  realtime: {
    key: "realtime",
    kind: "realtime",
    title: PART_JOBS.realtime.name,
    job: PART_JOBS.realtime.job,
    description: "Can watch the camera live when it supports video.",
  },
  avatar: {
    key: "avatar",
    kind: "avatar",
    title: PART_JOBS.avatar.name,
    job: PART_JOBS.avatar.job,
    description: "A talking-head video shown on the call's stage.",
  },
  image_gen: {
    key: "image_gen",
    kind: "image_gen",
    title: PART_JOBS.image_gen.name,
    job: PART_JOBS.image_gen.job,
    description: "Used by packs that draw sketches or illustrations, such as the insurance notebook.",
  },
  workflow_llm: {
    key: "workflow_llm",
    kind: "llm",
    title: PART_JOBS.workflow_llm.name,
    job: PART_JOBS.workflow_llm.job,
    description:
      "Used by packs for structured extraction. When unset: the main language model (cascaded) or a LiveKit Inference model (realtime).",
  },
  vad: {
    key: "vad",
    kind: "vad",
    title: PART_JOBS.vad.name,
    job: PART_JOBS.vad.job,
    description: "Notices when the caller is speaking. Unset uses Silero, running in the agent's worker.",
  },
  turn_detection: { key: "turn_detection", kind: "turn_detection", title: PART_JOBS.turn_detection.name, job: PART_JOBS.turn_detection.job },
  noise_cancellation: {
    key: "noise_cancellation",
    kind: "noise_cancellation",
    title: PART_JOBS.noise_cancellation.name,
    job: PART_JOBS.noise_cancellation.job,
    description: "Cleans up the caller's audio before it reaches the listener.",
  },
};

const PIPELINE_SLOTS: Record<PipelineMode, SlotKey[]> = {
  cascaded: ["stt", "llm", "tts"],
  realtime: ["realtime"],
  half_cascade: ["realtime", "tts"],
};

const ADVANCED_SLOTS: SlotKey[] = ["vad", "turn_detection", "noise_cancellation"];

const OPTIONAL_SLOTS: { key: SlotKey; add: string }[] = [
  { key: "avatar", add: "Add avatar" },
  { key: "image_gen", add: "Add image generation" },
  { key: "workflow_llm", add: "Advanced: workflow model" },
];

/** The api's endpoint-field rule (`routers/agents.py::_ENDPOINT_FIELD`, REVIEW-V2 R2-03). */
const ENDPOINT_FIELD = /(url|endpoint|host|api_base)$/i;

/** Shown on a provider endpoint field a builder may not set (V2-22, R-V2-33). */
export const ENDPOINT_FIELD_LOCKED_REASON =
  "Only admins and owners can set a provider endpoint, because the provider's key is sent to it.";

/** Read-only reason for an endpoint field below `admin`, `null` for any other field. */
export function endpointFieldLockedReason(field: FieldSpec): string | null {
  return ENDPOINT_FIELD.test(field.name) ? ENDPOINT_FIELD_LOCKED_REASON : null;
}

/**
 * Providers section (V2-13, replacing WP-4's `tabs/providers-tab.tsx` via the
 * `providers` `EditorExtension` — UI_UX_SPEC-V2-AMENDMENTS §2.3): adds the
 * half-cascade mode card (disabled with a reason when no realtime provider
 * on the bound connection has `text_modality`), makes every slot
 * connection-aware (`installed_on`/`enabled`/`cloud_only` via
 * `connectionDisabledReason`, R-V2-2), an Advanced disclosure for
 * VAD/Turn/NC, and the avatar's options (participant name, video quality,
 * timeouts).
 */
export function ProvidersSection({ agent: _agent }: EditorSectionProps) {
  const { data, isLoading, isError, error, refetch } = useProviders();
  const connectionsQuery = useConnections();
  const { control, watch, formState } = useFormContext<AgentEditorForm>();
  const mode = watch("config.pipeline.mode");
  const pipeline = watch("config.pipeline");
  const connectionId = watch("connection_id");
  const camera = watch("config.capabilities.camera");
  const screenShare = watch("config.capabilities.screen_share");
  const { issueFor } = useSectionIssues();
  // R-V2-33: adding or changing a provider endpoint (`base_url`, `*_url`,
  // `*endpoint`, `*host`) needs `admin`; the api refuses it for builders.
  const { canWrite: canSetEndpoints } = useWriteAccess("admin");

  const [expanded, setExpanded] = React.useState<SlotKey | null>(null);
  const [added, setAdded] = React.useState<Set<SlotKey>>(() => new Set());
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const [visionPickerOpen, setVisionPickerOpen] = React.useState(false);
  const [visionRequested, setVisionRequested] = React.useState(false);

  if (isLoading || connectionsQuery.isLoading) {
    return <ProvidersSkeleton />;
  }
  if (isError) {
    return <ErrorBanner error={error} context={{ action: "load the provider list" }} onRetry={() => refetch()} />;
  }

  const providers: ProviderOut[] = data?.providers ?? [];
  const connections = connectionsQuery.data?.items ?? [];
  const boundConnection = resolveBoundConnection(connectionId, connections);

  const constraints: SlotConstraints = {
    inference: boundConnection && boundConnection.capabilities?.inference_available === false ? "off" : "auto",
    disabledReason: (spec: ProviderSpec) => connectionDisabledReason(spec, boundConnection),
    fieldLockedReason: canSetEndpoints ? undefined : endpointFieldLockedReason,
    // V6-03 (ask #41): lets the pickers show the "Recommended" stack for this connection type.
    connection: boundConnection,
  };

  const pipelineErrors = formState.errors.config?.pipeline;
  const showVisionNote =
    mode === "cascaded" && (camera || screenShare) && isKnownTextOnlyLlm(providers, pipeline?.llm ?? null);

  const hasTextModalityRealtime = providers.some(
    (p) =>
      p.kind === "realtime" &&
      p.capabilities?.text_modality === true &&
      slotAvailability(p, boundConnection ? { connection: boundConnection } : {}) === "selectable",
  );

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

  /** The line under a part's job. Turn detection says who ends the turn when it is left unset (V6-33). */
  function slotDescription(key: SlotKey): string | undefined {
    if (key === "turn_detection") {
      const ender = whoEndsTurn({ pipeline: { ...pipeline, turn_detection: null }, providers, connection: boundConnection });
      return ender ? `If you leave this unset: ${turnEnderSentence(ender)}` : undefined;
    }
    return SLOTS[key].description;
  }

  /** A Cloud-versus-self-hosted note that belongs on one part's card. */
  function connectionNoteFor(key: SlotKey): string | null {
    if (key === "avatar") return avatarConnectionNote(boundConnection);
    if (key === "noise_cancellation") return noiseConnectionNote(boundConnection);
    return null;
  }

  function renderSlot(key: SlotKey, optional: boolean, extraConstraints?: Partial<SlotConstraints>) {
    const def = SLOTS[key];
    const problem = slotError(key);
    const isLlmWithNote = key === "llm" && showVisionNote;
    const connectionNote = connectionNoteFor(key);
    return (
      <Controller
        key={key}
        control={control}
        name={`config.pipeline.${key}`}
        render={({ field }) => (
          <ProviderSlotCard
            title={def.title}
            job={def.job}
            description={slotDescription(key)}
            kind={def.kind}
            value={field.value}
            onChange={field.onChange}
            constraints={{
              ...constraints,
              ...extraConstraints,
              required: !optional,
              preferVision: key === "llm" ? isLlmWithNote && visionRequested : undefined,
            }}
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
              ) : connectionNote ? (
                <p data-slot="connection-note" className="text-label leading-[1.125rem] text-pretty text-text-secondary">
                  {connectionNote}
                </p>
              ) : undefined
            }
          />
        )}
      />
    );
  }

  const pipelineSlots = PIPELINE_SLOTS[mode] ?? PIPELINE_SLOTS.cascaded;
  const halfCascadeInclude = { include: (spec: ProviderSpec) => spec.kind !== "realtime" || spec.capabilities?.text_modality === true };
  const optionalShown = OPTIONAL_SLOTS.filter(({ key }) => Boolean(pipeline?.[key as keyof typeof pipeline]) || added.has(key));
  const optionalHidden = OPTIONAL_SLOTS.filter(({ key }) => !pipeline?.[key as keyof typeof pipeline] && !added.has(key));
  const hasAvatar = Boolean(pipeline?.avatar);

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <PipelineSummaryLine pipeline={pipeline} providers={providers} connection={boundConnection} />
        <ConnectionNotes connection={boundConnection} />
      </div>

      <Group title="How it talks" description="Pick how speech flows through the agent. You can switch later; the other mode's choices are kept until you save.">
        <Controller
          control={control}
          name="config.pipeline.mode"
          render={({ field }) => (
            <ModeCards
              value={field.value}
              onChange={field.onChange}
              halfCascadeDisabledReason={
                hasTextModalityRealtime
                  ? null
                  : "No realtime model on this connection supports text output yet — half-cascade needs one (the same model thinks, a separate voice speaks)."
              }
            />
          )}
        />
      </Group>

      <Group title="Pipeline" description={pipelineSlots.length > 1 ? "In the order a turn flows through them." : undefined}>
        <ol className="flex flex-col" aria-label="Pipeline parts">
          {pipelineSlots.map((key, index) => (
            <li key={key} className="flex flex-col">
              {index > 0 ? <span aria-hidden="true" className="ml-7 h-4 w-px bg-border" /> : null}
              {renderSlot(key, false, mode === "half_cascade" && key === "realtime" ? halfCascadeInclude : undefined)}
            </li>
          ))}
        </ol>
      </Group>

      <Group title="Optional" description="Extras some packs use. Nothing here is needed for a call.">
        {optionalShown.length > 0 ? (
          <div className="flex flex-col gap-3">
            {optionalShown.map(({ key }) => (
              <React.Fragment key={key}>
                {renderSlot(key, true)}
                {key === "avatar" && hasAvatar ? (
                  <AvatarOptionsFields providerId={pipeline?.avatar?.provider_id ?? null} providers={providers} />
                ) : null}
              </React.Fragment>
            ))}
          </div>
        ) : null}
        {optionalHidden.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {optionalHidden.map(({ key, add }) => (
              <Button
                key={key}
                type="button"
                variant="secondary"
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

      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <CollapsibleTrigger className="group/adv inline-flex items-center gap-1 rounded-sm text-sm font-medium text-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
          <ChevronRightIcon className="size-4 transition-transform duration-(--duration-base) group-data-[state=open]/adv:rotate-90" aria-hidden="true" />
          Advanced: hearing speech, ending turns, filtering noise
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-3 flex flex-col gap-3">
          <p className="max-w-[65ch] text-label text-pretty text-text-secondary">
            You only need these to change the defaults. Left unset, the agent uses the connection&apos;s own choices.
          </p>
          {ADVANCED_SLOTS.map((key) => renderSlot(key, true))}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

function Group({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  const id = React.useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 id={id} className="text-dialog leading-6 font-semibold tracking-[-0.01em] text-balance text-foreground">
          {title}
        </h2>
        {description ? <p className="max-w-[65ch] text-label leading-[1.125rem] text-pretty text-text-secondary">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

const MODES: { value: Exclude<PipelineMode, never>; title: string; description: string; icon: LucideIcon }[] = [
  {
    value: "cascaded",
    title: "Cascaded",
    description: "Separate speech-to-text, language model and text-to-speech. Most flexible; works with LiveKit Inference without keys.",
    icon: WaypointsIcon,
  },
  {
    value: "realtime",
    title: "Realtime",
    description: "One speech-to-speech model (Gemini Live, GPT Realtime). Lowest latency; can watch the camera live.",
    icon: AudioWaveformIcon,
  },
  {
    value: "half_cascade",
    title: "Half-cascade",
    description: "A realtime model thinks, a separate voice speaks — pick the TTS voice yourself while keeping realtime's understanding.",
    icon: MessagesSquareIcon,
  },
];

function ModeCards({
  value,
  onChange,
  halfCascadeDisabledReason,
}: {
  value: PipelineMode;
  onChange: (next: PipelineMode) => void;
  halfCascadeDisabledReason: string | null;
}) {
  const name = React.useId();
  return (
    <div className="flex flex-col gap-2">
      <div role="radiogroup" aria-label="Pipeline mode" className="grid gap-2 sm:grid-cols-3">
        {MODES.map((mode) => {
          const disabled = mode.value === "half_cascade" && Boolean(halfCascadeDisabledReason) && value !== "half_cascade";
          return (
            <label
              key={mode.value}
              className={cn(
                "relative flex gap-3 rounded-lg border border-border bg-card p-4",
                disabled
                  ? "cursor-not-allowed opacity-60"
                  : cn(
                      "cursor-pointer transition-colors duration-(--duration-base) hover:bg-muted",
                      "has-[:checked]:border-brand-border has-[:checked]:bg-brand-subtle",
                      "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-background",
                    ),
              )}
            >
              <input
                type="radio"
                name={name}
                value={mode.value}
                checked={value === mode.value}
                disabled={disabled}
                onChange={() => onChange(mode.value)}
                className="sr-only"
              />
              <span
                aria-hidden="true"
                className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded bg-muted text-text-secondary"
              >
                <Icon as={mode.icon} size="md" />
              </span>
              <span className="flex min-w-0 flex-col gap-1">
                <span className="text-sm font-semibold text-foreground">{mode.title}</span>
                <span className="text-label leading-[1.125rem] text-pretty text-text-secondary">{mode.description}</span>
                {disabled ? <span className="text-label text-pretty text-warning-text">{halfCascadeDisabledReason}</span> : null}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

function VisionNote({ onShowVisionModels }: { onShowVisionModels: () => void }) {
  return (
    <div className="flex items-start gap-2 rounded bg-warning-subtle px-3 py-2 text-label leading-[1.125rem] text-warning-text">
      <Icon as={EyeOffIcon} size="sm" className="mt-0.5" />
      <p className="text-pretty">
        This model can&apos;t see images; pick one marked Vision to use the camera.{" "}
        <button
          type="button"
          onClick={onShowVisionModels}
          className="rounded-sm font-medium underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Show vision models
        </button>
      </p>
    </div>
  );
}

const VIDEO_QUALITIES = ["low", "medium", "high", "very_high"] as const;
const NONE = "__none__";

const FRAMING_OPTIONS: { value: AvatarFraming; label: string }[] = [
  { value: "auto", label: "Auto (recommended)" },
  { value: "portrait", label: "Portrait" },
  { value: "landscape", label: "Landscape" },
  { value: "square", label: "Square" },
];

const FIT_OPTIONS: { value: AvatarFit; label: string }[] = [
  { value: "contain", label: "Show the whole avatar" },
  { value: "cover", label: "Fill the space" },
];

const FRAMING_LABEL: Record<AvatarFraming, string> = {
  auto: "auto",
  portrait: "portrait",
  landscape: "landscape",
  square: "square",
};

/**
 * The avatar card's options (`AvatarOptions` — CONTRACTS-V2 §4.3), shown once
 * an avatar provider is picked. V6-26 adds the framing/fit pair and a
 * preview: since a `file`-type field like `bithuman-avatar`'s `avatar_image`
 * is a path on the agent worker (never an upload the browser can read), the
 * preview shows how the *chosen framing* looks rather than the true image's
 * aspect (docs/v6/_asks.md #154 tracks adding a real upload for those
 * fields), and warns only when the framing disagrees with the selected
 * provider's own documented native aspect.
 */
function AvatarOptionsFields({
  providerId,
  providers,
}: {
  providerId: string | null;
  providers: ProviderOut[];
}) {
  const { control, watch } = useFormContext<AgentEditorForm>();
  const provider = providers.find((p) => p.id === providerId);
  const declaredAspect = provider?.capabilities?.avatar_aspect ?? null;
  const declaredNote = provider?.capabilities?.avatar_aspect_note ?? null;
  const framing = watch("config.pipeline.avatar_options.framing");
  const fit = watch("config.pipeline.avatar_options.fit");
  const previewFraming: AvatarFraming = framing && framing !== "auto" ? framing : (declaredAspect ?? "auto");
  const frame = resolveFrame({ framing: previewFraming, fit });
  const mismatch = Boolean(
    framing && framing !== "auto" && declaredAspect && framing !== declaredAspect,
  );
  return (
    <div className="ml-4 flex flex-col gap-4 rounded border border-dashed border-border p-4 sm:ml-7">
      <h4 className="text-sm font-semibold text-foreground">Avatar options</h4>
      <div className="grid gap-4 sm:grid-cols-2">
        <Controller
          control={control}
          name="config.pipeline.avatar_options.participant_name"
          render={({ field }) => (
            <Field label="Participant name" htmlFor="avatar-participant-name">
              <Input id="avatar-participant-name" value={field.value} onChange={field.onChange} />
            </Field>
          )}
        />
        <Controller
          control={control}
          name="config.pipeline.avatar_options.video_quality"
          render={({ field }) => (
            <Field label="Video quality" htmlFor="avatar-video-quality" hint="Unset uses the avatar provider's default.">
              <Select value={field.value ?? NONE} onValueChange={(next) => field.onChange(next === NONE ? null : next)}>
                <SelectTrigger id="avatar-video-quality" className="w-full">
                  <SelectValue placeholder="Default" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>Default</SelectItem>
                  {VIDEO_QUALITIES.map((q) => (
                    <SelectItem key={q} value={q}>
                      {q.replace("_", " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
        />
        <Controller
          control={control}
          name="config.pipeline.avatar_options.idle_timeout_s"
          render={({ field }) => (
            <Field label="Idle timeout (s)" htmlFor="avatar-idle-timeout" hint="Unset never times out idle.">
              <Input
                id="avatar-idle-timeout"
                type="number"
                min={0}
                value={field.value ?? ""}
                onChange={(event) => field.onChange(event.target.value === "" ? null : Number(event.target.value))}
                className="tabular-nums"
              />
            </Field>
          )}
        />
        <Controller
          control={control}
          name="config.pipeline.avatar_options.max_duration_s"
          render={({ field }) => (
            <Field label="Max duration (s)" htmlFor="avatar-max-duration" hint="Unset has no cap.">
              <Input
                id="avatar-max-duration"
                type="number"
                min={0}
                value={field.value ?? ""}
                onChange={(event) => field.onChange(event.target.value === "" ? null : Number(event.target.value))}
                className="tabular-nums"
              />
            </Field>
          )}
        />
        <Controller
          control={control}
          name="config.pipeline.avatar_options.framing"
          render={({ field }) => (
            <Field
              label="Framing"
              htmlFor="avatar-framing"
              hint={
                declaredNote
                  ? declaredNote
                  : "How the avatar's own shape is sized — matches it automatically by default."
              }
            >
              <Select value={field.value ?? "auto"} onValueChange={field.onChange}>
                <SelectTrigger id="avatar-framing" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FRAMING_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
        />
        <Controller
          control={control}
          name="config.pipeline.avatar_options.fit"
          render={({ field }) => (
            <Field label="Fit" htmlFor="avatar-fit" hint="Filling the space can crop the top or sides.">
              <Select value={field.value ?? "contain"} onValueChange={field.onChange}>
                <SelectTrigger id="avatar-fit" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FIT_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
        />
      </div>

      <div className="flex items-start gap-4">
        <div
          data-testid="avatar-framing-preview"
          data-framing={previewFraming}
          data-fit={frame.objectFit}
          className="bg-stage flex w-24 shrink-0 items-center justify-center overflow-hidden rounded"
          style={{ aspectRatio: aspectRatioStyle(frame.aspectRatio) }}
        >
          <Icon
            as={UserRoundIcon}
            size="lg"
            className="text-stage-foreground opacity-60"
            style={frame.objectFit === "cover" ? { transform: "translateY(-20%)" } : undefined}
          />
        </div>
        <div className="flex flex-col gap-1 text-xs text-text-secondary">
          <p>
            Preview: {FRAMING_LABEL[previewFraming]} framing, {frame.objectFit === "cover" ? "filling the space" : "showing the whole avatar"}.
          </p>
          {mismatch && (
            <p className="flex items-start gap-1.5 text-warning-text">
              <Icon as={TriangleAlertIcon} size="sm" className="mt-0.5 shrink-0" />
              <span>
                This provider&rsquo;s avatar usually streams {FRAMING_LABEL[declaredAspect as AvatarFraming]} video —
                picking {FRAMING_LABEL[framing as AvatarFraming]} may letterbox it. Auto matches it automatically.
              </span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function ProvidersSkeleton() {
  return (
    <LoadingRegion label="Loading providers" className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-32" />
        <div className="grid gap-2 sm:grid-cols-3">
          <Skeleton className="h-24 w-full" />
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
