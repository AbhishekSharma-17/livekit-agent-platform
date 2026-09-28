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
  /**
   * V5-31: the languages the agent may speak (first = default), automatic detection and a
   * voice per language. Optional so fixtures built before V5-31 keep validating; carried
   * here so saving the editor never drops them (V5-35's Languages card edits them).
   */
  languages: z.array(z.string()).optional(),
  auto_detect: z.boolean().optional(),
  voices_by_language: z.record(z.string(), providerRefSchema).optional(),
});

export const capabilitiesConfigSchema = z.object({
  camera: z.boolean(),
  screen_share: z.boolean(),
  chat_input: z.boolean(),
  vision_inject_per_turn: z.boolean(),
});

/**
 * `NotifyTeamConfig` (V5-25, `lkap_contracts.tools`): where `notify_team` (and
 * `escalate_to_human`) posts a summary. `credential_id` names an
 * `http-tool-secret` key holding the webhook URL — never the URL itself.
 */
export const notifyTeamConfigSchema = z.object({
  credential_id: z.string().min(1, "Choose a key"),
  include_transcript: z.boolean().optional(),
  on_escalation: z.boolean().optional(),
  secret_name: z.string().optional(),
  style: z.enum(["slack", "generic"]).optional(),
});
export type NotifyTeamConfigForm = z.infer<typeof notifyTeamConfigSchema>;

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
   * V5-25's curated network built-ins (V5-28's Tools-tab slots edit these):
   * without them here, `z.object`'s resolver parse would strip every one of
   * them from a save (the file header's warning) — the slots' edits would
   * never persist. `web_search`/`sms` reuse `providerRefSchema` (the same
   * shape pipeline slots use); `fetch_url_allowed_hosts` is site names only
   * (no scheme, no path — the api refuses anything else); `notify_team` is
   * `null` until the "Notify your team" card is turned on.
   */
  fetch_url_allowed_hosts: z.array(z.string().min(1)).max(50, "50 sites max").optional(),
  web_search: providerRefSchema.nullable().optional(),
  sms: providerRefSchema.nullable().optional(),
  notify_team: notifyTeamConfigSchema.nullable().optional(),
  /**
   * V5-48 (`connected-apps-card.tsx`): `AppsMode` (docs/v5/COMPOSIO.md
   * D-V5-C6) is a passthrough for the same reason as `builtin_execution`
   * above — no JSON-Schema-to-zod step exists, so its nested shape (`mode`,
   * `allowed_toolkits`, `denied_actions`, `router.{search,execute,
   * manage_connections}`, and — V5-54, R-V5-13 — `accounts: {toolkit:
   * [connection_id]}`, the per-app account chooser's value) is mirrored by
   * hand instead of deeply validated. Without this field `z.object` would
   * strip `config.tools.apps` on every resolver parse (the file header's
   * warning), silently discarding the mode picker's value — and, for
   * `accounts`, which account(s) a multi-account app should use — on save.
   * Optional (unlike `builtin_execution`) so the many fixtures across this
   * codebase's tests that build a `tools` value by hand, from before V5-47,
   * don't all need updating; `DEFAULT_TOOLS` (`agents/defaults.ts`) always
   * supplies it in the real editor.
   */
  apps: z.custom<AppsMode>().optional(),
});

/**
 * `KnowledgeConfig` (`contracts/src/lkap_contracts/agent_config.py`). The v2
 * retrieval fields (V5-06) are optional here for the same reason `apps` and
 * `locale` are above: a fixture built by hand before V5-10 that constructs a
 * `config.knowledge` value without them must still round-trip through
 * `zodResolver` without the keys getting stripped from the save payload (the
 * file header's warning) — `DEFAULT_KNOWLEDGE` (`agents/defaults.ts`) always
 * supplies concrete values in the real editor.
 */
export const knowledgeConfigSchema = z.object({
  kb_ids: z.array(z.string()),
  auto_inject: z.boolean(),
  top_k: z.number().int().min(1, "At least 1").max(20, "20 max"),
  min_score: z.number().min(0).max(1).nullable().optional(),
  prefetch: z.boolean().optional(),
  /** `"none" | "local"` today; a `connection:<id>` string is allowed from V5-24 on. */
  rerank: z.string().optional(),
  mode: z.enum(["vector", "hybrid"]).optional(),
  max_inject_tokens: z.number().int().min(1, "At least 1").optional(),
  skip_short_turns: z.boolean().optional(),
  query_mode: z.enum(["last_turn", "conversation"]).optional(),
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

/**
 * `RecordingConfig` (CONTRACTS-V2 §4.3). Audio-only is fixed on in Phase 1.
 * `require_consent`/`consent_text` (V5-15, `docs/v5/_asks.md`, V5-17's edit
 * outside its exclusive files): without them here `z.object`'s resolver
 * parse would strip both from every save (this file's header warning) — the
 * Recording section's "Ask for consent before recording" switch would never
 * persist.
 */
export const recordingConfigSchema = z.object({
  enabled: z.boolean(),
  audio_only: z.boolean(),
  storage_config_id: z.string().nullable(),
  retention_days: wholeNumber("Whole days only").min(1, "At least 1 day").nullable(),
  require_consent: z.boolean(),
  consent_text: z.string().max(2000, "2000 characters max").nullable(),
});
export type RecordingConfigForm = z.infer<typeof recordingConfigSchema>;

/**
 * `DisclosureConfig` (V5-15, D-V5-22; V5-17's edit outside its exclusive
 * files, same rationale as `recordingConfigSchema` above). Optional on
 * `agentConfigFormSchema` like `localeConfigSchema` — a hand-built fixture
 * built before this field existed doesn't need to supply it, and
 * `toFormValues` always fills a concrete value from `DEFAULT_DISCLOSURE`.
 */
export const disclosureConfigSchema = z.object({
  enabled: z.boolean(),
  position: z.enum(["greeting", "banner", "both"]),
  text: z.string().max(2000, "2000 characters max").nullable(),
});
export type DisclosureConfigForm = z.infer<typeof disclosureConfigSchema>;

/**
 * `AgentTest` (V5-29, `lkap_contracts.agent_tests`): one simulated
 * conversation. `mocks` is a tool name → the fixture value it returns
 * instead of calling out; the console edits it as text and the case dialog
 * `JSON.parse`s it, falling back to the raw string (the contract's "a string
 * is returned as-is; anything else as JSON").
 */
export const agentTestSchema = z.object({
  id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/, "Letters, numbers, - or _; must start with a letter or number"),
  name: z.string().min(1, "Name is required").max(120, "120 characters max"),
  persona_instructions: z.string().min(1, "Describe the caller").max(4000, "4000 characters max"),
  scenario: z.string().max(4000, "4000 characters max"),
  expectations: z.array(z.string().min(1, "Can't be empty").max(500, "500 characters max")).max(20, "20 max"),
  mocks: z.record(z.string(), z.unknown()),
  max_turns: z.number().int().min(1, "At least 1").max(40, "40 max"),
});
export type AgentTestForm = z.infer<typeof agentTestSchema>;

/**
 * `PublishGate` (V5-29): opt-in "require a passing test run before publish".
 * Like `disclosureConfigSchema` above, optional on `agentConfigFormSchema` so
 * a fixture built before V5-33 keeps validating.
 */
export const publishGateSchema = z.object({
  require_tests: z.boolean(),
  min_pass_ratio: z.number().min(0).max(1),
});
export type PublishGateForm = z.infer<typeof publishGateSchema>;

/**
 * `PrivacyConfig` (V5-30, `lkap_contracts.agent_config`): what the platform
 * hides in transcripts, keeps after a call, and shares with third-party
 * analytics. Optional on `agentConfigFormSchema` like `disclosureConfigSchema`
 * above — `toFormValues` always supplies a concrete value from `DEFAULT_PRIVACY`.
 */
export const privacyConfigSchema = z.object({
  stt_redact: z.array(z.enum(["pci", "pii", "phi", "numbers"])),
  storage_tier: z.enum(["full", "redacted", "basic"]),
  telemetry_pii: z.boolean(),
  scrub_model: providerRefSchema.nullable(),
});
export type PrivacyConfigForm = z.infer<typeof privacyConfigSchema>;

/**
 * `MemoryConfig` (V5-40, `lkap_contracts.agent_config`): whether the agent
 * remembers a returning caller between calls, its scope, retention, the
 * spoken consent line and how much is recalled at session start. Optional on
 * `agentConfigFormSchema` like `privacyConfigSchema` above — `toFormValues`
 * always supplies a concrete value from `DEFAULT_MEMORY`. Ranges mirror the
 * Pydantic model exactly (`retention_days` 1..3650, `consent_line` <= 500
 * chars, `max_recall_tokens` 50..2000) so the client and server agree.
 */
export const memoryConfigSchema = z.object({
  enabled: z.boolean(),
  scope: z.enum(["agent", "workspace"]),
  retention_days: z.number().int().min(1, "At least 1 day").max(3650, "3650 days max"),
  consent_line: z.string().max(500, "500 characters max").nullable(),
  max_recall_tokens: z.number().int().min(50, "At least 50").max(2000, "2000 max"),
  verbatim: z.boolean(),
});
export type MemoryConfigForm = z.infer<typeof memoryConfigSchema>;

/**
 * `GuardrailsConfig` / `RegexRule` / `ClassifierRule` / `ProviderRule` (V5-39,
 * `lkap_contracts.guardrails`): rules on what the caller says, what the agent
 * says and what a tool returns. `kind` is a required literal on each rule
 * schema (the generated `lkap-contracts.d.ts` marks it optional — a
 * `json-schema-to-typescript` default rendering — but the form needs it
 * concrete to discriminate the union), so `guardrailRuleSchema` can be a
 * `z.discriminatedUnion` the way the api's own `Annotated[..., discriminator]`
 * reads it. Limits mirror the Pydantic model exactly: `name` 1..60,
 * `pattern` 1..300, `prompt` 1..1000, `safe_reply` 1..500, `budget_ms`
 * 50..2000, at most 20 rules per stage. The api's own `_unique_names`
 * `model_validator` (case-insensitive per stage) does not come back as a
 * clean `issues[].path`, so `guardrailsConfigSchema`'s `superRefine` below
 * catches it client-side too.
 */
export const regexRuleSchema = z.object({
  kind: z.literal("regex"),
  name: z.string().min(1, "Name is required").max(60, "60 characters max"),
  pattern: z.string().min(1, "Pattern is required").max(300, "300 characters max"),
  ignore_case: z.boolean(),
});
export type RegexRuleForm = z.infer<typeof regexRuleSchema>;

export const classifierRuleSchema = z.object({
  kind: z.literal("classifier"),
  name: z.string().min(1, "Name is required").max(60, "60 characters max"),
  prompt: z.string().min(1, "Instruction is required").max(1000, "1000 characters max"),
});
export type ClassifierRuleForm = z.infer<typeof classifierRuleSchema>;

export const MODERATION_CATEGORY_VALUES = [
  "harassment",
  "harassment/threatening",
  "hate",
  "hate/threatening",
  "illicit",
  "illicit/violent",
  "self-harm",
  "self-harm/intent",
  "self-harm/instructions",
  "sexual",
  "sexual/minors",
  "violence",
  "violence/graphic",
] as const;

export const providerRuleSchema = z.object({
  kind: z.literal("provider"),
  name: z.string().min(1, "Name is required").max(60, "60 characters max"),
  provider: z.literal("openai_moderation"),
  categories: z.array(z.enum(MODERATION_CATEGORY_VALUES)),
  credential_id: z.string().nullable(),
});
export type ProviderRuleForm = z.infer<typeof providerRuleSchema>;

export const guardrailRuleSchema = z.discriminatedUnion("kind", [
  regexRuleSchema,
  classifierRuleSchema,
  providerRuleSchema,
]);
export type GuardrailRuleForm = z.infer<typeof guardrailRuleSchema>;

const guardrailRuleListSchema = z.array(guardrailRuleSchema).max(20, "20 rules max");

export const guardrailsConfigSchema = z
  .object({
    input: guardrailRuleListSchema,
    output: guardrailRuleListSchema,
    tool_output: guardrailRuleListSchema,
    on_trip: z.enum(["interrupt", "end_call", "escalate"]),
    safe_reply: z.string().min(1, "The safe reply can't be empty").max(500, "500 characters max"),
    model: providerRefSchema.nullable(),
    budget_ms: z.number().int().min(50, "At least 50 ms").max(2000, "2000 ms max"),
  })
  .superRefine((val, ctx) => {
    (["input", "output", "tool_output"] as const).forEach((stage) => {
      const seen = new Set<string>();
      val[stage].forEach((rule, index) => {
        const key = rule.name.trim().toLowerCase();
        if (key && seen.has(key)) {
          ctx.addIssue({
            code: "custom",
            path: [stage, index, "name"],
            message: `Two ${stage === "input" ? "caller" : stage === "output" ? "agent" : "tool"} rules can't share a name.`,
          });
        }
        seen.add(key);
      });
    });
  });
export type GuardrailsConfigForm = z.infer<typeof guardrailsConfigSchema>;

/**
 * `QaField` (V5-30): one post-call field the judge fills in. `name` mirrors
 * the contract's lowercase-identifier pattern so a bad name is caught before
 * save rather than as a 422.
 */
export const qaFieldSchema = z
  .object({
    name: z
      .string()
      .min(1, "Name is required")
      .regex(/^[a-z][a-z0-9_]{0,47}$/, "Lowercase letters, numbers, underscore; must start with a letter"),
    type: z.enum(["text", "number", "boolean", "select"]),
    options: z.array(z.string().min(1, "Can't be empty").max(100, "100 characters max")).max(50, "50 max"),
    description: z.string().max(500, "500 characters max"),
  })
  .superRefine((val, ctx) => {
    if (val.type === "select" && val.options.length === 0) {
      ctx.addIssue({ code: "custom", path: ["options"], message: "A select field needs at least one option" });
    }
    if (val.type !== "select" && val.options.length > 0) {
      ctx.addIssue({ code: "custom", path: ["options"], message: "Only a select field takes options" });
    }
  });
export type QaFieldForm = z.infer<typeof qaFieldSchema>;

/**
 * `QaConfig` (post-call scoring). Only `fields` is bound to an input here
 * (the Post-call fields editor, V5-34); `enabled`/`rubric_prompt`/`model`
 * have no console editor yet and must still round-trip a save, hence
 * `.catchall(z.unknown())` — without it `z.object`'s resolver parse would
 * silently drop them (this file's header warning), turning QA back off on
 * the next save of any agent that had it on.
 */
export const qaConfigSchema = z
  .object({ fields: z.array(qaFieldSchema).max(20, "20 fields max") })
  .catchall(z.unknown());
export type QaConfigForm = z.infer<typeof qaConfigSchema>;

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
  /**
   * V5-32: `cold` (hand over at once) or `warm` (the agent briefs the person first; LiveKit
   * Cloud only). Optional so a target saved before V5-32 validates; carried so a save never
   * turns a warm target cold (V5-36 edits it).
   */
  mode: z.enum(["cold", "warm"]).optional(),
});
export type TransferTargetForm = z.infer<typeof transferTargetSchema>;

/** `SmsTarget` (V5-25, `lkap_contracts.telephony`): a saved contact `send_sms` may text besides the caller. */
export const SMS_TARGET_PATTERN = /^\+[1-9]\d{6,14}$/;

export const smsTargetSchema = z.object({
  label: z.string().trim().min(1, "Name the contact").max(64, "64 characters max"),
  to: z
    .string()
    .trim()
    .max(32, "32 characters max")
    .regex(SMS_TARGET_PATTERN, "Use international format, e.g. +15551234567"),
});
export type SmsTargetForm = z.infer<typeof smsTargetSchema>;

export const telephonyConfigSchema = z
  .object({
    transfer_targets: z.array(transferTargetSchema).max(50, "50 destinations max"),
    /**
     * V5-25 (`DEFAULT_TELEPHONY`, `agents/defaults.ts`); V5-28 edits it here so a
     * save actually carries the Tools section's "Send a text message" contacts
     * instead of the resolver stripping them (see `toolsConfigSchema` above).
     * Optional like `apps`/`locale`: a fixture built before this field existed
     * doesn't need it, and `toFormValues` always supplies a concrete list.
     */
    sms_targets: z.array(smsTargetSchema).max(50, "50 contacts max").optional(),
    /**
     * V5-32: answering-machine detection on outbound calls (`AmdConfig`). Optional and carried
     * so a save keeps it; V5-36's Voicemail card edits it.
     */
    amd: z
      .object({
        enabled: z.boolean(),
        on_machine: z.enum(["hangup", "leave_message"]),
        message: z.string().max(1000, "1000 characters max").nullable().optional(),
        ivr_detection: z.boolean(),
      })
      .optional(),
  })
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
    const seenSms = new Set<string>();
    (val.sms_targets ?? []).forEach((target, index) => {
      const key = target.label.trim().toLowerCase();
      if (key && seenSms.has(key)) {
        ctx.addIssue({
          code: "custom",
          path: ["sms_targets", index, "label"],
          message: "Two contacts can't share a name.",
        });
      }
      seenSms.add(key);
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
  "consent",
  "upload",
  "captions",
  "handoff",
  "link",
  "slots",
  "cards",
  "notebook",
  "layout",
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
    /**
     * `PanelLayout.accept_state_delta` (V5-43, ask #309): the caller's page may change some
     * display blocks. Carried so a save never drops it; the switch comes with V5-44.
     */
    accept_state_delta: z.boolean().optional(),
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
    /**
     * V5-15's AI disclosure (D-V5-22); optional like `locale` above — the
     * Conversation section's "Disclosure" card (V5-17) is the only editor,
     * and `toFormValues` always supplies a concrete value from
     * `DEFAULT_DISCLOSURE`.
     */
    disclosure: disclosureConfigSchema.optional(),
    /**
     * V5-33's Tests section: `config.tests` (optional, like `disclosure`
     * above, so a fixture built before this package keeps validating) and
     * the opt-in publish gate.
     */
    tests: z.array(agentTestSchema).max(50, "50 cases max").optional(),
    publish_gate: publishGateSchema.optional(),
    /**
     * V5-34's Privacy card and the post-call fields editor: `config.privacy`
     * (optional, like `disclosure` above) and `config.qa` (the `.catchall`
     * keeps `enabled`/`rubric_prompt`/`model`; only `fields` is edited here).
     */
    privacy: privacyConfigSchema.optional(),
    qa: qaConfigSchema.optional(),
    /**
     * V5-42's Memory card: `config.memory` (optional, like `privacy` above —
     * a fixture built before this package keeps validating; `toFormValues`
     * always supplies a concrete value from `DEFAULT_MEMORY`).
     */
    memory: memoryConfigSchema.optional(),
    /**
     * V5-41's Guardrails card: `config.guardrails` (optional, like `memory`
     * above — a fixture built before this package keeps validating;
     * `toFormValues` always supplies a concrete value from
     * `DEFAULT_GUARDRAILS`).
     */
    guardrails: guardrailsConfigSchema.optional(),
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
    // V5-33: two cases can't share an id (`lkap_contracts.agent_config._unique_test_ids`).
    const seenTestIds = new Set<string>();
    (val.tests ?? []).forEach((testCase, index) => {
      if (seenTestIds.has(testCase.id)) {
        ctx.addIssue({ code: "custom", path: ["tests", index, "id"], message: "Two cases can't share an id." });
      }
      seenTestIds.add(testCase.id);
    });
    // V5-34: two post-call fields can't share a name (`lkap_contracts.agent_config.QaConfig._unique_field_names`).
    const seenFieldNames = new Set<string>();
    (val.qa?.fields ?? []).forEach((field, index) => {
      if (seenFieldNames.has(field.name)) {
        ctx.addIssue({ code: "custom", path: ["qa", "fields", index, "name"], message: "Two fields can't share a name." });
      }
      seenFieldNames.add(field.name);
    });
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

/**
 * `McpAuth` (`lkap_contracts.tools`, V5-09): a discriminated union mirror, not a flat form
 * field — `mcp-tool-editor-dialog.tsx` builds one of these three shapes from its own radio
 * state before posting `definition.auth` (docs/v5/_asks.md #66: `auth` only, never the
 * deprecated `headers`/`credential_id` top-level mirrors).
 */
export const mcpNoAuthSchema = z.object({ kind: z.literal("none") });
export const mcpHeaderAuthSchema = z.object({
  kind: z.literal("header"),
  headers: z.record(z.string(), z.string()),
  credential_id: z.string().nullable().optional(),
});
export const mcpOAuthAuthSchema = z.object({
  kind: z.literal("oauth"),
  credential_id: z.string().nullable().optional(),
  registration: z.enum(["auto", "preregistered"]),
  client_id: z.string().nullable().optional(),
  client_secret_ref: z.string().nullable().optional(),
  scopes: z.array(z.string()).nullable().optional(),
  subject: z.enum(["workspace", "agent"]),
});
export const mcpAuthSchema = z.discriminatedUnion("kind", [mcpNoAuthSchema, mcpHeaderAuthSchema, mcpOAuthAuthSchema]);
export type McpAuthForm = z.infer<typeof mcpAuthSchema>;

export const mcpToolFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  url: z.string().min(1, "URL is required"),
  auth: mcpAuthSchema.optional(),
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
