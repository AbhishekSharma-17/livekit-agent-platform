import type {
  AgentLimits,
  AvatarOptions,
  CapabilitiesConfig,
  KnowledgeConfig,
  RecordingConfig,
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
};

export const DEFAULT_KNOWLEDGE: Required<KnowledgeConfig> = {
  kb_ids: [],
  auto_inject: true,
  top_k: 4,
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

/** `AgentLimits` defaults (CONTRACTS-V2 §3.3). */
export const DEFAULT_LIMITS: Required<AgentLimits> = {
  max_concurrent_sessions: 5,
  max_session_duration_s: 1800,
  rate_per_ip_per_min: 6,
  rate_per_agent_per_min: 60,
};
