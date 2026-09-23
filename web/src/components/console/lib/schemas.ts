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

export const pipelineConfigSchema = z
  .object({
    mode: z.enum(["realtime", "cascaded", "half_cascade"]),
    realtime: providerRefSchema.nullable().optional(),
    stt: providerRefSchema.nullable().optional(),
    llm: providerRefSchema.nullable().optional(),
    tts: providerRefSchema.nullable().optional(),
    avatar: providerRefSchema.nullable().optional(),
    image_gen: providerRefSchema.nullable().optional(),
    workflow_llm: providerRefSchema.nullable().optional(),
    turn_handling: z.record(z.string(), z.unknown()).optional(),
  })
  .superRefine((val, ctx) => {
    if (val.mode === "realtime" && !val.realtime) {
      ctx.addIssue({ code: "custom", path: ["realtime"], message: "Choose a realtime model." });
    }
    if (val.mode === "cascaded") {
      if (!val.stt) ctx.addIssue({ code: "custom", path: ["stt"], message: "Choose a speech-to-text provider." });
      if (!val.llm) ctx.addIssue({ code: "custom", path: ["llm"], message: "Choose an LLM provider." });
      if (!val.tts) ctx.addIssue({ code: "custom", path: ["tts"], message: "Choose a text-to-speech provider." });
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

export const agentConfigFormSchema = z.object({
  instructions: z.string().min(1, "Instructions are required"),
  pipeline: pipelineConfigSchema,
  voice: voiceConfigSchema,
  capabilities: capabilitiesConfigSchema,
  tools: toolsConfigSchema,
  knowledge: knowledgeConfigSchema,
  pack_settings: z.record(z.string(), z.unknown()),
  timezone: z.string().min(1, "Timezone is required"),
});
export type AgentConfigForm = z.infer<typeof agentConfigFormSchema>;

export const agentEditorFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  description: z.string(),
  ui_panel_id: z.string().min(1, "Panel is required"),
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
