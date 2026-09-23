import { MessageSquareIcon, MonitorUpIcon, ScanEyeIcon, VideoIcon, type LucideIcon } from "lucide-react";

/**
 * Console-only constants that aren't part of the generated contracts.
 *
 * Built-in tool names mirror docs/ARCHITECTURE.md §7.3 / IMPLEMENTATION_PLAN
 * W1-AGENT-TOOLS (`agent/src/lkap_agent/tools/builtin/*.py`). `http_request`
 * is intentionally excluded here — it has its own
 * `AgentConfig.tools.http_request_enabled` switch because it grants outbound
 * network access.
 */
export interface BuiltinToolInfo {
  name: string;
  label: string;
  help: string;
}

export const BUILTIN_TOOLS: BuiltinToolInfo[] = [
  { name: "end_call", label: "End call", help: "Lets the agent end the session on request." },
  {
    name: "search_knowledge",
    label: "Search knowledge",
    help: "Explicit knowledge-base lookup (in addition to auto-inject).",
  },
  {
    name: "describe_current_frame",
    label: "Describe current frame",
    help: "Describes the latest camera/screen frame via a vision model.",
  },
  { name: "pin_frame", label: "Pin frame", help: "Pins the latest video frame to the UI as a photo." },
  { name: "push_note", label: "Push note", help: "Lets the agent add a note to the UI panel." },
  { name: "set_status", label: "Set status", help: "Lets the agent set the UI status stamp." },
  {
    name: "escalate_to_human",
    label: "Escalate to human",
    help: "Flags the session as needing human follow-up.",
  },
  { name: "current_time", label: "Current time", help: "Returns the current date/time in the agent's timezone." },
];

export const PROVIDER_KIND_LABELS: Record<string, string> = {
  realtime: "Realtime model",
  stt: "Speech-to-text",
  llm: "LLM",
  tts: "Text-to-speech",
  avatar: "Avatar",
  image_gen: "Image generation",
  embedding: "Embedding",
  secret_bag: "Secret bag",
};

/**
 * Token classes per status tone (docs/UI_UX_SPEC.md §2.2). Prefer
 * `StatusChip` from `@/components/shared/status-chip` for status display.
 */
export const TONE_BADGE_CLASSES: Record<string, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-info-soft text-info-text",
  success: "bg-success-soft text-success-text",
  warning: "bg-warning-soft text-warning-text",
  danger: "bg-danger-soft text-danger-text",
};

/**
 * Panel-block tools (agent `tools/builtin/__init__.py::BLOCK_TOOL_NAMES`,
 * asks #69). The worker registers each only when the panel has a block it
 * writes (see `BLOCK_TOOL_TYPES` in `@/panels/blocks/catalog`), so the panel
 * composer shows these switches next to the blocks rather than in the Tools
 * section; `config.tools.builtin_disabled` turns them off like any built-in.
 * Deliberately not in `BUILTIN_TOOLS` (the worker keeps them out of
 * `BUILTIN_TOOL_NAMES` too).
 */
export const BLOCK_TOOLS: BuiltinToolInfo[] = [
  {
    name: "update_block",
    label: "Update blocks",
    help: "Lets the agent change what a table, document, gallery, sources, transcript, video or pack block shows.",
  },
  { name: "show_document", label: "Show documents", help: "Lets the agent open a document on a page and highlight it." },
  { name: "table_append", label: "Add table rows", help: "Lets the agent add rows to a table as it collects them." },
  { name: "request_form", label: "Ask with a form", help: "Lets the agent ask the caller to fill in a form and wait for it." },
];

export type CapabilityKey = "camera" | "screen_share" | "chat_input" | "vision_inject_per_turn";

export interface CapabilityMeta {
  label: string;
  description: string;
  /** Lucide icon name (kebab case) — see `icon` for the component. */
  iconName: string;
  icon: LucideIcon;
}

/** Session capabilities as shown in the editor and on pre-call (§2.6, §4.7). */
export const CAPABILITY_META: Record<CapabilityKey, CapabilityMeta> = {
  camera: {
    label: "Camera",
    description: "Callers can turn on their camera; the agent sees frames when the model supports vision.",
    iconName: "video",
    icon: VideoIcon,
  },
  screen_share: {
    label: "Screen share",
    description: "Callers can share a window or tab.",
    iconName: "monitor-up",
    icon: MonitorUpIcon,
  },
  chat_input: {
    label: "Typing",
    description: "Callers can type instead of speaking.",
    iconName: "message-square",
    icon: MessageSquareIcon,
  },
  vision_inject_per_turn: {
    label: "Look at the latest frame every turn",
    description: "Sends the newest camera or screen frame with each reply (costs vision tokens).",
    iconName: "scan-eye",
    icon: ScanEyeIcon,
  },
};
