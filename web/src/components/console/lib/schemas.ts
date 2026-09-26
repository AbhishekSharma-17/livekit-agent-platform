import { z } from "zod";

import type { AppsMode, FlowSpec, ToolExecution } from "@/contracts/lkap-contracts";

/**
 * Hand-authored zod mirrors of the generated contract interfaces
 * (`@/contracts/lkap-contracts`, produced by `lkap_contracts.export` — see
 * docs/CONTRACTS.md §6). There is no JSON-Schema-to-zod step wired into the
 * Wave 0 build, so these are kept in lockstep with the generated TS
 * interfaces by hand; a mismatch here is a bug in this file, not a
 * reinterpretation of the contract.
 *
 * Used only for client-side form validation before submit. The api's own
 * Pydantic validation (`POST /v1/agents/{id}/validate`) remains the
 * authoritative check — see `ValidationBanner`.
 */

export const fieldValueSchema = z.union([z.string(), z.number(), z.boolean()]);

export const providerRefSchema = z.object({
  provider_id: z.string().min(1, "Choose a provider"),
  credential_id: z.string().nullable().optional(),
  model: z.string().nullable().optional(),
  fields: z.record(z.string(), fieldValueSchema).optional(),
});
export type ProviderRefForm = z.infer<typeof providerRefSchema>;

/**
 * `AvatarOptions` (CONTRACTS-V2 §4.3) — added to the form by V2-13 so the
 * providers section's avatar card can edit it (`README.md` rule 2: a field
 * missing from the form until a section needs it). Logged as a V2-13 edit
 * outside its exclusive files in `docs/v2/_asks.md`.
 */
export const avatarOptionsSchema = z.object({
  participant_name: z.string(),
  video_quality: z.enum(["low", "medium", "high", "very_high"]).nullable(),
  idle_timeout_s: z.number().int().nullable(),
  max_duration_s: z.number().int().nullable(),
});
export type AvatarOptionsForm = z.infer<typeof avatarOptionsSchema>;

/**
 * `TurnHandlingOptions` and its nested SDK mirrors (V5-07,
 * `lkap_contracts.turn_handling`): every field optional (unset = the SDK's
 * own default at session start) and every level keeps unknown keys via
 * `.catchall` — the api's models use `extra="allow"` for the same reason
 * (a newer SDK's key still round-trips), and `zodResolver` (`lib/zod-resolver.ts`)
 * submits `schema.safeParse(values).data`, which **drops** any key a plain
 * `z.object()` doesn't know about; without `.catchall` the Conversation
 * section's "Advanced" JSON editor (V5-11) would silently lose them on Save.
 */
export const endpointingOptionsSchema = z
  .object({
    mode: z.enum(["fixed", "dynamic"]).nullable().optional(),
    min_delay: z.number().min(0).nullable().optional(),
    max_delay: z.number().min(0).nullable().optional(),
    alpha: z.number().min(0).max(1).nullable().optional(),
  })
  .catchall(z.unknown());
export type EndpointingOptionsForm = z.infer<typeof endpointingOptionsSchema>;

export const interruptionOptionsSchema = z
  .object({
    enabled: z.boolean().nullable().optional(),
    mode: z.enum(["adaptive", "vad"]).nullable().optional(),
    min_duration: z.number().min(0).nullable().optional(),
    min_words: z.number().int().min(0).nullable().optional(),
    false_interruption_timeout: z.number().min(0).nullable().optional(),
    resume_false_interruption: z.boolean().nullable().optional(),
  })
  .catchall(z.unknown());
export type InterruptionOptionsForm = z.infer<typeof interruptionOptionsSchema>;

export const preemptiveGenerationOptionsSchema = z
  .object({
    enabled: z.boolean().nullable().optional(),
    preemptive_tts: z.boolean().nullable().optional(),
    max_speech_duration: z.number().min(0).nullable().optional(),
    max_retries: z.number().int().min(0).nullable().optional(),
  })
  .catchall(z.unknown());
export type PreemptiveGenerationOptionsForm = z.infer<typeof preemptiveGenerationOptionsSchema>;

export const userTurnLimitOptionsSchema = z
  .object({
    max_words: z.number().int().min(1).nullable().optional(),
    max_duration: z.number().min(0).nullable().optional(),
  })
  .catchall(z.unknown());
export type UserTurnLimitOptionsForm = z.infer<typeof userTurnLimitOptionsSchema>;

export const turnHandlingOptionsSchema = z
  .object({
    /** Set by the platform; the api refuses it here (kept so a stored value still validates). */
    turn_detection: z.enum(["stt", "vad", "realtime_llm", "manual"]).nullable().optional(),
    endpointing: endpointingOptionsSchema.nullable().optional(),
    interruption: interruptionOptionsSchema.nullable().optional(),
    preemptive_generation: preemptiveGenerationOptionsSchema.nullable().optional(),
    user_turn_limit: userTurnLimitOptionsSchema.nullable().optional(),
  })
  .catchall(z.unknown());
export type TurnHandlingOptionsForm = z.infer<typeof turnHandlingOptionsSchema>;

/** `TurnDetectorSettings` (V5-07): where the end-of-turn model runs and its sensitivity. */
export const turnDetectorSettingsSchema = z.object({
  mode: z.enum(["hosted", "local"]).nullable().optional(),
  unlikely_threshold: z.number().min(0).max(1).nullable().optional(),
});
export type TurnDetectorSettingsForm = z.infer<typeof turnDetectorSettingsSchema>;

/** `PipelineConfig.conversation_preset` (V5-07). Optional: fixtures built before this field don't need it. */
export const CONVERSATION_PRESET_VALUES = ["patient", "balanced", "snappy", "telephony", "custom"] as const;

/**
 * True when every own value of `obj` is `undefined` or `null` (an object
 * `zodResolver` would otherwise submit as `{}`/all-`null`). `null` counts as
 * empty here because every leaf this is used on documents `None`/`null` as
 * "unset" (never a distinct third state) — and RHF's own default-value
 * cloning, observed empirically, fills an unmounted nested `Controller`'s
 * leaf with `null` rather than leaving it `undefined` when its parent
 * object's own default was `null` (e.g. a stored `turn_detector: null`).
 */
function isEffectivelyEmpty(obj: Record<string, unknown>): boolean {
  return Object.values(obj).every((value) => value === undefined || value === null);
}

/**
 * Mounting a `Controller` for a leaf that was never set (e.g. `interruption.min_words`
 * with no `interruption` in the stored config at all) makes RHF materialize the whole
 * path, so `turnHandlingOptionsSchema`'s parse turns an absent `interruption` into a
 * present-but-empty `{}` — and `{}` is not `undefined`, so it round-trips onto the save
 * payload as a real (if inert) change to a config the user never touched. This drops
 * any `endpointing`/`interruption`/`preemptive_generation`/`user_turn_limit` that
 * parsed to "every field unset", so choosing a preset (or editing an unrelated field)
 * truly "leaves `turn_handling` untouched" (V5-11 acceptance) rather than padding it
 * with empty siblings on every save.
 */
function pruneEmptyTurnHandlingGroups(value: TurnHandlingOptionsForm): TurnHandlingOptionsForm {
  const cleaned = { ...value };
  for (const key of ["endpointing", "interruption", "preemptive_generation", "user_turn_limit"] as const) {
    const nested = cleaned[key];
    if (nested && typeof nested === "object" && isEffectivelyEmpty(nested)) {
      delete cleaned[key];
    }
  }
  return cleaned;
}

export const pipelineConfigSchema = z
  .object({
    mode: z.enum(["realtime", "cascaded", "half_cascade"]),
    realtime: providerRefSchema.nullable().optional(),
    stt: providerRefSchema.nullable().optional(),
    llm: providerRefSchema.nullable().optional(),
    tts: providerRefSchema.nullable().optional(),
    avatar: providerRefSchema.nullable().optional(),
    avatar_options: avatarOptionsSchema,
    image_gen: providerRefSchema.nullable().optional(),
    workflow_llm: providerRefSchema.nullable().optional(),
    /** Advanced slots (V2-13): `null` = the connection/Inference default. */
    vad: providerRefSchema.nullable().optional(),
    turn_detection: providerRefSchema.nullable().optional(),
    noise_cancellation: providerRefSchema.nullable().optional(),
    /** V5-07/V5-11 (`editor/sections/conversation-section.tsx`): turn-taking, presets, the detector. */
    turn_handling: turnHandlingOptionsSchema.optional(),
    conversation_preset: z.enum(CONVERSATION_PRESET_VALUES).optional(),
    turn_detector: turnDetectorSettingsSchema.nullable().optional(),
  })
  .superRefine((val, ctx) => {
    // Slot requirements per mode (CONTRACTS-V2 §4.3): realtime → realtime;
    // cascaded → stt + llm + tts; half_cascade → realtime (text modality) + tts.
    if ((val.mode === "realtime" || val.mode === "half_cascade") && !val.realtime) {
      ctx.addIssue({ code: "custom", path: ["realtime"], message: "Choose a realtime model." });
    }
    if (val.mode === "cascaded") {
      if (!val.stt) ctx.addIssue({ code: "custom", path: ["stt"], message: "Choose a speech-to-text provider." });
      if (!val.llm) ctx.addIssue({ code: "custom", path: ["llm"], message: "Choose a language model." });
    }
    if ((val.mode === "cascaded" || val.mode === "half_cascade") && !val.tts) {
      ctx.addIssue({ code: "custom", path: ["tts"], message: "Choose a text-to-speech provider." });
    }
  })
  .transform((val) => {
    // See `pruneEmptyTurnHandlingGroups` above: undo the RHF-mounting artefact for
    // both `turn_handling`'s nested groups and `turn_detector` itself (an
    // effectively-empty `turn_detector` goes back to `null`, its usual "unset" shape).
    const cleaned = { ...val };
    if (cleaned.turn_handling) cleaned.turn_handling = pruneEmptyTurnHandlingGroups(cleaned.turn_handling);
    if (cleaned.turn_detector && isEffectivelyEmpty(cleaned.turn_detector)) cleaned.turn_detector = null;
    return cleaned;
  });

export const voiceConfigSchema = z.object({
  greeting: z.string().min(1, "Greeting is required"),
  greeting_mode: z.enum(["say", "generate"]),
  language: z.string().min(1, "Language is required"),
  allow_interruptions: z.boolean(),
  user_away_timeout_s: z.number().nullable().optional(),
  /** V4-13: the Conversation section's thinking-sound picker (BACKGROUND-TOOLS.md D-V4-38). */
  thinking_sound: z.enum(["none", "keyboard_typing", "keyboard_typing2", "office_ambience"]),
  /**
   * V5-07/V5-11: the Conversation section's background-sound picker. Optional
   * (like `locale`/`tools.apps` above) so fixtures built before V5-07 don't
   * need updating; `DEFAULT_VOICE` (`agents/defaults.ts`) always supplies
   * `"none"` in the real editor.
   */
  ambient_sound: z.string().optional(),
});

export const capabilitiesConfigSchema = z.object({
  camera: z.boolean(),
  screen_share: z.boolean(),
  chat_input: z.boolean(),
  vision_inject_per_turn: z.boolean(),
});

export const toolsConfigSchema = z.object({
  builtin_disabled: z.array(z.string()),
  http_request_enabled: z.boolean(),
  tool_ids: z.array(z.string()),
  max_tool_steps: z.number().int().min(1, "At least 1").max(20, "20 max"),
  /**
   * V4-13 (BACKGROUND-TOOLS.md §7): `execution_default` is the Conversation
   * card's "Read tools run" picker; `builtin_execution` is written by
   * `builtin-execution-dialog.tsx` (the Tools tab's per-builtin "Execution"
   * chip), not by direct binding, so it is a passthrough — the dialog owns
   * the real shape (`ToolExecution`, mirrored by hand since there is no
   * JSON-Schema-to-zod step, see the file header).
   */
  execution_default: z.enum(["blocking", "background", "auto"]),
  builtin_execution: z.record(z.string(), z.custom<ToolExecution>()),
  /**
   * V5-48 (`connected-apps-card.tsx`): `AppsMode` (docs/v5/COMPOSIO.md
   * D-V5-C6) is a passthrough for the same reason as `builtin_execution`
   * above — no JSON-Schema-to-zod step exists, so its nested shape (`mode`,
   * `allowed_toolkits`, `denied_actions`, `router.{search,execute,
   * manage_connections}`) is mirrored by hand instead of deeply validated.
   * Without this field `z.object` would strip `config.tools.apps` on every
   * resolver parse (the file header's warning), silently discarding the
   * mode picker's value on save. Optional (unlike `builtin_execution`) so
   * the many fixtures across this codebase's tests that build a `tools`
   * value by hand, from before V5-47, don't all need updating; `DEFAULT_TOOLS`
   * (`agents/defaults.ts`) always supplies it in the real editor.
   */
  apps: z.custom<AppsMode>().optional(),
});

export const knowledgeConfigSchema = z.object({
  kb_ids: z.array(z.string()),
  auto_inject: z.boolean(),
  top_k: z.number().int().min(1, "At least 1").max(20, "20 max"),
});

/**
 * `LocaleConfig` (R-V5-10, `contracts/src/lkap_contracts/agent_config.py`).
 * Optional here (like `tools.apps` above) so fixtures built before V5-52
 * that construct a `config` value by hand don't all need updating; the
 * Instructions tab's "Caller's time" radio (V5-52) is the only editor of
 * this field, and `DEFAULT_LOCALE` (`agents/defaults.ts`) always supplies a
 * concrete value in the real editor. Without this field in the schema,
 * `z.object`'s resolver parse would strip `config.locale` from every save
 * (the file header's warning) — the radio's edits would never persist.
 */
export const localeConfigSchema = z.object({
  caller_timezone: z.enum(["detect", "business"]).optional(),
});

const wholeNumber = (message: string) => z.number({ error: "Enter a number" }).int(message);

/** `RecordingConfig` (CONTRACTS-V2 §4.3). Audio-only is fixed on in Phase 1. */
export const recordingConfigSchema = z.object({
  enabled: z.boolean(),
  audio_only: z.boolean(),
  storage_config_id: z.string().nullable(),
  retention_days: wholeNumber("Whole days only").min(1, "At least 1 day").nullable(),
});
export type RecordingConfigForm = z.infer<typeof recordingConfigSchema>;

/** `AgentLimits` (CONTRACTS-V2 §3.3) — top-level on the agent, not in `config`. */
export const agentLimitsSchema = z.object({
  max_concurrent_sessions: wholeNumber("Whole numbers only").min(1, "At least 1"),
  max_session_duration_s: wholeNumber("Whole seconds only").min(60, "At least 60 seconds"),
  rate_per_ip_per_min: wholeNumber("Whole numbers only").min(1, "At least 1"),
  rate_per_agent_per_min: wholeNumber("Whole numbers only").min(1, "At least 1"),
});
export type AgentLimitsForm = z.infer<typeof agentLimitsSchema>;

/**
 * `TransferTarget` / `TelephonyConfig` (R-V2-21, `lkap_contracts.telephony`). Mirrors the
 * contract's pattern; the api also checks every `to` against the workspace's dialing policy.
 */
export const TRANSFER_TARGET_PATTERN = /^(\+[1-9]\d{6,14}|tel:\+?[0-9]{3,20}|sips?:[^\s@]+@\S+)$/;

export const transferTargetSchema = z.object({
  label: z.string().trim().min(1, "Name the destination").max(64, "64 characters max"),
  to: z
    .string()
    .trim()
    .max(256, "256 characters max")
    .regex(TRANSFER_TARGET_PATTERN, "Use +15551234567, tel:+15551234567 or sip:user@host"),
});
export type TransferTargetForm = z.infer<typeof transferTargetSchema>;

export const telephonyConfigSchema = z
  .object({ transfer_targets: z.array(transferTargetSchema).max(50, "50 destinations max") })
  .superRefine((val, ctx) => {
    const seen = new Set<string>();
    val.transfer_targets.forEach((target, index) => {
      const key = target.label.trim().toLowerCase();
      if (key && seen.has(key)) {
        ctx.addIssue({
          code: "custom",
          path: ["transfer_targets", index, "label"],
          message: "Two destinations can't share a name.",
        });
      }
      seen.add(key);
    });
  });
export type TelephonyConfigForm = z.infer<typeof telephonyConfigSchema>;

/** `"*"` or a bare origin (`https://example.com[:port]`, no path). */
export const ORIGIN_PATTERN = /^(\*|https?:\/\/[a-z0-9.-]+(:\d{1,5})?)$/i;

/** `BlockSpec.type` (CONTRACTS-V2 §4.4). */
export const BLOCK_TYPE_VALUES = [
  "status",
  "notes",
  "checklist",
  "activity",
  "form",
  "document",
  "gallery",
  "table",
  "transcript",
  "video",
  "kb_citations",
  "custom",
  "choices",
  "details",
  "markdown",
  "steps",
] as const;

/** Block ids key `UiState.blocks` and appear in patch paths: no `/`, no spaces. */
export const BLOCK_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/** `BlockSpec` (CONTRACTS-V2 §4.4; `title` is in the model even though the generated TS drops it). */
export const blockSpecSchema = z.object({
  id: z.string().regex(BLOCK_ID_PATTERN, "Use letters, numbers, - or _ (no spaces)"),
  type: z.enum(BLOCK_TYPE_VALUES),
  title: z.string().max(80, "80 characters max").nullable(),
  config: z.record(z.string(), z.unknown()),
  order: z.number().int(),
});
export type BlockSpecForm = z.infer<typeof blockSpecSchema>;

/** `PanelLayout` (CONTRACTS-V2 §4.3) — edited by the panel composer (V2-11). */
export const panelLayoutSchema = z
  .object({
    panel_id: z.string().min(1, "Choose a panel"),
    layout: z.enum(["side", "wide"]),
    blocks: z.array(blockSpecSchema),
  })
  .superRefine((val, ctx) => {
    const seen = new Set<string>();
    val.blocks.forEach((block, index) => {
      if (seen.has(block.id)) {
        ctx.addIssue({ code: "custom", path: ["blocks", index, "id"], message: "Two blocks can't share an id." });
      }
      seen.add(block.id);
    });
  });
export type PanelLayoutForm = z.infer<typeof panelLayoutSchema>;

export const agentConfigFormSchema = z
  .object({
    /**
     * Required for prompt agents; a flow agent may leave it empty and run on its global
     * node alone (R-V2-13, asks V2-16-5) — enforced by the `superRefine` below.
     */
    instructions: z.string(),
    pipeline: pipelineConfigSchema,
    voice: voiceConfigSchema,
    capabilities: capabilitiesConfigSchema,
    tools: toolsConfigSchema,
    knowledge: knowledgeConfigSchema,
    pack_settings: z.record(z.string(), z.unknown()),
    timezone: z.string().min(1, "Timezone is required"),
    /** R-V5-10: whose clock the agent talks in at session start (see `localeConfigSchema`). */
    locale: localeConfigSchema.optional(),
    recording: recordingConfigSchema,
    panel: panelLayoutSchema,
    /**
     * `config.flow`, edited by the flow builder (V2-16). Lax on purpose: the
     * builder checks the graph itself (`components/console/flow/flow-model.ts`)
     * and the api validates it on save. `null` = a prompt agent (R-V2-12).
     */
    flow: z.custom<FlowSpec | null>().optional(),
    /** `config.telephony` (R-V2-21): the transfer destinations, edited in the Tools section. */
    telephony: telephonyConfigSchema,
  })
  .superRefine((val, ctx) => {
    // A flow agent (`config.flow` has nodes) may run on its global node alone (R-V2-13).
    const isFlow = (val.flow?.nodes?.length ?? 0) > 0;
    if (!isFlow && !val.instructions.trim()) {
      ctx.addIssue({ code: "custom", path: ["instructions"], message: "Instructions are required" });
    }
  });
export type AgentConfigForm = z.infer<typeof agentConfigFormSchema>;

/**
 * The agent editor's form. Only the fields a section edits live here; the
 * rest of the stored `AgentConfig` v2 (`panel`, `qa`, `flow`, the pipeline's
 * `vad`/`turn_detection`/`noise_cancellation`/`avatar_options`, …) is merged
 * back from the loaded agent at save time (`editor/form-values.ts`), because
 * `z.object` strips unknown keys from the parsed values. A section that
 * starts editing one of those fields adds it here first.
 */
export const agentEditorFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  description: z.string(),
  ui_panel_id: z.string().min(1, "Panel is required"),
  mode: z.enum(["prompt", "flow"]),
  /** The bound LiveKit connection; null = the workspace default (V2-13 edits it). */
  connection_id: z.string().nullable(),
  limits: agentLimitsSchema,
  allowed_origins: z.array(z.string().regex(ORIGIN_PATTERN, "Use an origin like https://example.com, or *")),
  config: agentConfigFormSchema,
});
export type AgentEditorForm = z.infer<typeof agentEditorFormSchema>;

export const createAgentFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  description: z.string(),
  pack_id: z.string().min(1, "Choose a pack"),
});
export type CreateAgentForm = z.infer<typeof createAgentFormSchema>;

export const createKbFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  description: z.string(),
  embedder_id: z.string().min(1, "Choose an embedder"),
});
export type CreateKbForm = z.infer<typeof createKbFormSchema>;

export const credentialSecretsSchema = z.record(z.string(), z.string());

export const createCredentialFormSchema = z.object({
  label: z.string().min(1, "Label is required"),
  secrets: credentialSecretsSchema,
});
export type CreateCredentialForm = z.infer<typeof createCredentialFormSchema>;

export const httpToolFormSchema = z.object({
  name: z
    .string()
    .min(1, "Name is required")
    .regex(/^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/, "Use letters, numbers, underscore; start with a letter or underscore"),
  description: z.string().min(1, "Description is required"),
  parametersJson: z.string().refine((value) => {
    try {
      const parsed: unknown = JSON.parse(value);
      return typeof parsed === "object" && parsed !== null;
    } catch {
      return false;
    }
  }, "Must be a valid JSON Schema object"),
  method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]),
  url: z.string().min(1, "URL is required"),
  headersJson: z.string().refine((value) => {
    if (value.trim() === "") return true;
    try {
      const parsed: unknown = JSON.parse(value);
      return typeof parsed === "object" && parsed !== null;
    } catch {
      return false;
    }
  }, "Must be valid JSON"),
  credential_id: z.string().nullable().optional(),
  body_template: z.string().nullable().optional(),
  allowed_hosts: z.string(),
  timeout_s: z.number().min(1).max(120),
  max_result_chars: z.number().int().min(100).max(20000),
  result_path: z.string().nullable().optional(),
  silent_reply: z.boolean(),
  enabled: z.boolean(),
});
export type HttpToolForm = z.infer<typeof httpToolFormSchema>;

export const mcpToolFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  url: z.string().min(1, "URL is required"),
  headersJson: z.string().refine((value) => {
    if (value.trim() === "") return true;
    try {
      const parsed: unknown = JSON.parse(value);
      return typeof parsed === "object" && parsed !== null;
    } catch {
      return false;
    }
  }, "Must be valid JSON"),
  credential_id: z.string().nullable().optional(),
  allowed_tools: z.string(),
  timeout_s: z.number().min(1).max(120),
  sse_read_timeout_s: z.number().min(1).max(3600),
  enabled: z.boolean(),
});
export type McpToolForm = z.infer<typeof mcpToolFormSchema>;
