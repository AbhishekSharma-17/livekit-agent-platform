import type {
  AgentLimits,
  AvatarOptions,
  CapabilitiesConfig,
  KnowledgeConfig,
  LocaleConfig,
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
};

/** `AvatarOptions` defaults (CONTRACTS-V2 §4.3; added by V2-13 for the providers section's avatar card). */
export const DEFAULT_AVATAR_OPTIONS: Required<AvatarOptions> = {
  participant_name: "Avatar",
  video_quality: null,
  idle_timeout_s: null,
  max_duration_s: null,
};

/** `TelephonyConfig` defaults (R-V2-21): no transfer destinations. */
export const DEFAULT_TELEPHONY: Required<TelephonyConfig> = {
  transfer_targets: [],
};

/** `AgentLimits` defaults (CONTRACTS-V2 §3.3). */
export const DEFAULT_LIMITS: Required<AgentLimits> = {
  max_concurrent_sessions: 5,
  max_session_duration_s: 1800,
  rate_per_ip_per_min: 6,
  rate_per_agent_per_min: 60,
};
