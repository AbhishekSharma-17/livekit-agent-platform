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

/**
 * Known `PackManifest.ui_panel_id` values shipped by packs in this
 * repository (docs/IMPLEMENTATION_PLAN.md `generic`, `insurance_claim`).
 * The panel select offers these plus free text so a future pack's panel id
 * isn't blocked on this console being redeployed — W1-WEB-SESSION's
 * `web/src/panels/registry.ts` (built in parallel) is the runtime source of
 * truth and falls back to `generic` for anything it doesn't recognise.
 */
export const KNOWN_PANEL_IDS = ["generic", "insurance_notebook"] as const;

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

export interface PanelMeta {
  label: string;
  description: string;
  /** Session layout the panel asks for (`PanelDefinition.layout`). */
  layout: "side" | "wide";
}

/** Human metadata for the known `ui_panel_id`s (§4.7). Unknown ids fall back to `generic`. */
export const PANEL_META: Record<string, PanelMeta> = {
  generic: {
    label: "Session panel",
    description: "Status, notes, checklist, attachments and activity. Works with every pack.",
    layout: "side",
  },
  insurance_notebook: {
    label: "Claim notebook",
    description:
      "The adjuster's notebook: handwritten notes, taped photos, sketch and stamp. Needs the insurance pack's tools.",
    layout: "wide",
  },
};

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
