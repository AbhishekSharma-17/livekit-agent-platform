import type {
  AgentLimits,
  AvatarOptions,
  CapabilitiesConfig,
  DisclosureConfig,
  GuardrailsConfig,
  KnowledgeConfig,
  LocaleConfig,
  MemoryConfig,
  PrivacyConfig,
  PublishGate,
  RecordingConfig,
  TelephonyConfig,
  ToolsConfig,
  VoiceConfig,
} from "@/contracts/lkap-contracts";

/**
 * Mirrors the Pydantic defaults in `AgentConfig`'s nested models
 * (docs/CONTRACTS.md §6). The generated TS marks these sub-objects optional
 * because the api always fills them in, but the editor's react-hook-form
 * state needs concrete values to bind controlled inputs to.
 */
export const DEFAULT_VOICE: Required<VoiceConfig> = {
  greeting: "Hello! How can I help you today?",
  greeting_mode: "say",
  language: "en",
  allow_interruptions: true,
  user_away_timeout_s: 15.0,
  first_speaker: "agent",
  thinking_sound: "none",
  // V5-07: no background clip unless the agent picks one (V5-11's Conversation section edits it).
  ambient_sound: "none",
  // V5-31: one language, no detection, the pipeline's voice (V5-35's Languages card edits these).
  languages: [],
  auto_detect: false,
  voices_by_language: {},
};

export const DEFAULT_CAPABILITIES: Required<CapabilitiesConfig> = {
  camera: false,
  screen_share: false,
  chat_input: true,
  vision_inject_per_turn: true,
  dtmf: false,
};

export const DEFAULT_TOOLS: Required<ToolsConfig> = {
  builtin_disabled: [],
  http_request_enabled: false,
  tool_ids: [],
  max_tool_steps: 3,
  execution_default: "blocking",
  builtin_execution: {},
  // V5-47: connected apps are off unless the agent opts in (V5-48's card edits this).
  apps: {
    mode: "off",
    allowed_toolkits: [],
    denied_actions: [],
    router: { search: true, execute: true, manage_connections: false },
  },
  // V5-25: the network built-ins are off until their service is set (V5-28's slots edit these).
  web_search: null,
  sms: null,
  fetch_url_allowed_hosts: [],
  notify_team: null,
};

export const DEFAULT_KNOWLEDGE: Required<KnowledgeConfig> = {
  kb_ids: [],
  auto_inject: true,
  top_k: 4,
  // V5-06: retrieval settings (V5-10's Knowledge tab edits them).
  min_score: null,
  prefetch: true,
  rerank: "none",
  mode: "hybrid",
  max_inject_tokens: 1200,
  skip_short_turns: true,
  query_mode: "conversation",
};

/** `LocaleConfig` defaults (R-V5-10): the caller's own timezone unless the agent opts into the business one. */
export const DEFAULT_LOCALE: Required<LocaleConfig> = {
  caller_timezone: "detect",
};

/** `RecordingConfig` defaults (CONTRACTS-V2 §4.3). */
export const DEFAULT_RECORDING: Required<RecordingConfig> = {
  enabled: false,
  audio_only: true,
  storage_config_id: null,
  retention_days: null,
  // V5-15: record as before unless the author asks for consent first.
  require_consent: false,
  consent_text: null,
};

/**
 * `DisclosureConfig` defaults (V5-15, D-V5-22): on, in both places, using the
 * workspace's wording — mirrors the stored default so a fixture built before
 * this field existed still resolves to the same on-by-default behaviour.
 * V5-17's edit outside its exclusive files (`agents/defaults.ts`), same
 * rationale as `DEFAULT_RECORDING`'s `require_consent`/`consent_text` above.
 */
export const DEFAULT_DISCLOSURE: Required<DisclosureConfig> = {
  enabled: true,
  position: "both",
  text: null,
};

/** `PrivacyConfig` defaults (V5-30): nothing masked, kept in full, analytics gets the conversation. */
export const DEFAULT_PRIVACY: Required<PrivacyConfig> = {
  stt_redact: [],
  storage_tier: "full",
  telemetry_pii: true,
  scrub_model: null,
};

/** `PublishGate` defaults (V5-29): off, and every case must pass once turned on. */
export const DEFAULT_PUBLISH_GATE: Required<PublishGate> = {
  require_tests: false,
  min_pass_ratio: 1,
};

/** `MemoryConfig` defaults (V5-40): off, per-agent, 90 days, no consent line, a bounded recall. */
export const DEFAULT_MEMORY: Required<MemoryConfig> = {
  enabled: false,
  scope: "agent",
  retention_days: 90,
  consent_line: null,
  max_recall_tokens: 400,
  verbatim: false,
};

/**
 * `GuardrailsConfig` defaults (V5-39/V5-41): every rule list empty (no check,
 * no added latency), a trip stops the agent and speaks the safe reply, no
 * model of its own (falls back to the agent's workflow model), a 300 ms
 * budget. `safe_reply` mirrors `lkap_contracts.guardrails.DEFAULT_SAFE_REPLY`.
 */
export const DEFAULT_GUARDRAILS: Required<GuardrailsConfig> = {
  input: [],
  output: [],
  tool_output: [],
  on_trip: "interrupt",
  safe_reply: "I'm sorry, I can't help with that. Is there anything else I can help you with?",
  model: null,
  budget_ms: 300,
};

/**
 * `AvatarOptions` defaults (CONTRACTS-V2 §4.3; added by V2-13 for the providers section's avatar
 * card). `framing`/`fit` (V6-26) default to `"auto"`/`"contain"` here for the *form* only — the
 * wire value stays `null` (unset) until the builder saves, so a stored agent nobody has touched
 * yet is unaffected; `null` already renders identically to `"auto"`/`"contain"` (the crop-free
 * default), so this is display-only, not a behaviour change.
 */
export const DEFAULT_AVATAR_OPTIONS: Required<AvatarOptions> = {
  participant_name: "Avatar",
  video_quality: null,
  idle_timeout_s: null,
  max_duration_s: null,
  framing: "auto",
  fit: "contain",
};

/** `TelephonyConfig` defaults (R-V2-21): no transfer destinations. */
export const DEFAULT_TELEPHONY: Required<TelephonyConfig> = {
  transfer_targets: [],
  // V5-25: numbers `send_sms` may text by label (V5-28's telephony editor).
  sms_targets: [],
  // V5-32: answering-machine detection on outbound calls, off (V5-36's Voicemail card).
  amd: { enabled: false, on_machine: "hangup", message: null, ivr_detection: false },
};

/** `AgentLimits` defaults (CONTRACTS-V2 §3.3). */
export const DEFAULT_LIMITS: Required<AgentLimits> = {
  max_concurrent_sessions: 5,
  max_session_duration_s: 1800,
  rate_per_ip_per_min: 6,
  rate_per_agent_per_min: 60,
};
