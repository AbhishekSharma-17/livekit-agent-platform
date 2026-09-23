import { z } from "zod";

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
    turn_handling: z.record(z.string(), z.unknown()).optional(),
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
  });

export const voiceConfigSchema = z.object({
  greeting: z.string().min(1, "Greeting is required"),
  greeting_mode: z.enum(["say", "generate"]),
  language: z.string().min(1, "Language is required"),
  allow_interruptions: z.boolean(),
  user_away_timeout_s: z.number().nullable().optional(),
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
});

export const knowledgeConfigSchema = z.object({
  kb_ids: z.array(z.string()),
  auto_inject: z.boolean(),
  top_k: z.number().int().min(1, "At least 1").max(20, "20 max"),
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

export const agentConfigFormSchema = z.object({
  instructions: z.string().min(1, "Instructions are required"),
  pipeline: pipelineConfigSchema,
  voice: voiceConfigSchema,
  capabilities: capabilitiesConfigSchema,
  tools: toolsConfigSchema,
  knowledge: knowledgeConfigSchema,
  pack_settings: z.record(z.string(), z.unknown()),
  timezone: z.string().min(1, "Timezone is required"),
  recording: recordingConfigSchema,
  panel: panelLayoutSchema,
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
