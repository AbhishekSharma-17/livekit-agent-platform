/**
 * Pure state logic for the session surface (docs/UI_UX_SPEC.md §5.4).
 *
 * Everything here is free of React, LiveKit and the DOM so the whole state
 * model is enumerable from tests and from the preview route (WP-10): the
 * room-wired container maps hooks onto `toAgentUiState()` and hands the
 * result to `StageView`.
 */
import {
  AGENT_STATE_LABEL,
  type AgentUiState,
} from "@/components/shared/agent-state";
import type { PanelConnectionState } from "@/lib/livekit";

/* -------------------------------------------------------------------------- */
/* Agent state                                                                */
/* -------------------------------------------------------------------------- */

export interface AgentUiStateInput {
  /** `toPanelConnectionState(session.connectionState)`. */
  connectionState: PanelConnectionState;
  /** `useAgent().state` — the LiveKit `AgentState` string. */
  agentState: string;
  /** True once the room has been connected at least once in this attempt. */
  hasConnected: boolean;
  /** A connect-level error (token source / `start()`), if any. */
  error?: string | null;
}

/**
 * The §5.4 state model.
 *
 * Precedence: a failure wins over everything, then the room's own transport
 * states, then the agent's. `idle` (the agent SDK's "connected but between
 * turns" state) reads as `listening` once the room is up — the visitor's
 * microphone is open either way — and as `connecting` before that.
 */
export function toAgentUiState({
  connectionState,
  agentState,
  hasConnected,
  error,
}: AgentUiStateInput): AgentUiState {
  if (error || agentState === "failed") return "failed";
  if (connectionState === "reconnecting") return "reconnecting";
  if (connectionState === "disconnected") {
    return hasConnected ? "ended" : "connecting";
  }
  if (connectionState === "connecting") return "connecting";
  switch (agentState) {
    case "listening":
    case "thinking":
    case "speaking":
      return agentState;
    case "idle":
      return "listening";
    default:
      // `connecting` | `initializing` | `pre-connect-buffering` | `disconnected`
      return "connecting";
  }
}

/** Stage caption per §5.4 (the ellipsis is part of the caption, not the label). */
export const AGENT_STATE_CAPTION: Record<AgentUiState, string> = {
  connecting: `${AGENT_STATE_LABEL.connecting}…`,
  listening: AGENT_STATE_LABEL.listening,
  thinking: `${AGENT_STATE_LABEL.thinking}…`,
  speaking: AGENT_STATE_LABEL.speaking,
  reconnecting: `${AGENT_STATE_LABEL.reconnecting}…`,
  failed: "Couldn't join",
  ended: "Call ended",
};

/* -------------------------------------------------------------------------- */
/* Controls                                                                   */
/* -------------------------------------------------------------------------- */

export type SessionControlKey =
  | "leave"
  | "microphone"
  | "camera"
  | "screenShare"
  | "chat";

export interface ControlCapabilities {
  camera?: boolean | null;
  screen_share?: boolean | null;
  chat_input?: boolean | null;
}

export interface VisibleControlsInput {
  state: AgentUiState;
  capabilities: ControlCapabilities;
  /** `false` on phones: `getDisplayMedia` is unsupported, so the control is hidden, not disabled. */
  screenShareSupported: boolean;
}

/**
 * Which controls the vendored `AgentControlBar` renders (§5.4). A failed call
 * keeps only "Leave"; an unsupported screen share is hidden rather than shown
 * disabled.
 */
export function visibleControls({
  state,
  capabilities,
  screenShareSupported,
}: VisibleControlsInput): Record<SessionControlKey, boolean> {
  if (state === "failed") {
    return {
      leave: true,
      microphone: false,
      camera: false,
      screenShare: false,
      chat: false,
    };
  }
  return {
    leave: true,
    microphone: true,
    camera: Boolean(capabilities.camera),
    screenShare: Boolean(capabilities.screen_share) && screenShareSupported,
    chat: Boolean(capabilities.chat_input),
  };
}

/**
 * Controls that are visible but inert for the current state (§5.4): while the
 * room is connecting or reconnecting only the microphone stays usable.
 *
 * The control bar is vendored and takes no per-control `disabled`, so the
 * wrapper enforces this by swallowing clicks and dimming the locked controls
 * (`components/session/session-controls.tsx`).
 */
export function lockedControls(state: AgentUiState): SessionControlKey[] {
  switch (state) {
    case "connecting":
    case "reconnecting":
      return ["camera", "screenShare", "chat"];
    case "ended":
      return ["microphone", "camera", "screenShare", "chat"];
    default:
      return [];
  }
}

/** `aria-label`s the vendored bar puts on each control (used by the wrapper's CSS). */
export const CONTROL_LABEL: Record<Exclude<SessionControlKey, "leave">, string> =
  {
    microphone: "Toggle microphone",
    camera: "Toggle camera",
    screenShare: "Toggle screen share",
    chat: "Toggle transcript",
  };

/** `getDisplayMedia` is absent on iOS Safari and most Android browsers. */
export function screenShareSupported(): boolean {
  if (typeof navigator === "undefined") return false;
  return typeof navigator.mediaDevices?.getDisplayMedia === "function";
}

/* -------------------------------------------------------------------------- */
/* Copy helpers                                                               */
/* -------------------------------------------------------------------------- */

/** Elapsed timer in the top strip: `4:12`, `1:04:12`. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const seconds = total % 60;
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  const mm = hours > 0 ? String(minutes).padStart(2, "0") : String(minutes);
  return `${hours > 0 ? `${hours}:` : ""}${mm}:${String(seconds).padStart(2, "0")}`;
}

/** End-of-call sentence duration (§5.6): "4 min 12 s", "45 s", "1 h 5 min". */
export function formatCallDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const seconds = total % 60;
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  if (hours > 0) return `${hours} h ${minutes} min`;
  if (minutes > 0) return `${minutes} min ${seconds} s`;
  return `${seconds} s`;
}

/* -------------------------------------------------------------------------- */
/* Pre-call "What to expect"                                                  */
/* -------------------------------------------------------------------------- */

export type ExpectationIcon = "mic" | "video" | "screen" | "chat";

export interface Expectation {
  icon: ExpectationIcon;
  text: string;
}

/**
 * The three-to-four line "what to expect" list of §5.1, derived from the
 * agent's capabilities. The first line is always the voice line.
 */
export function expectations(
  agentName: string,
  capabilities: ControlCapabilities,
): Expectation[] {
  const items: Expectation[] = [
    {
      icon: "mic",
      text: `${agentName} speaks first and listens while you talk`,
    },
  ];
  if (capabilities.camera) {
    items.push({
      icon: "video",
      text: "You can turn on your camera to show something",
    });
  }
  if (capabilities.screen_share) {
    items.push({ icon: "screen", text: "You can share your screen" });
  }
  if (capabilities.chat_input) {
    items.push({ icon: "chat", text: "You can also type" });
  }
  return items;
}

/* -------------------------------------------------------------------------- */
/* Microphone permission copy (§5.1)                                          */
/* -------------------------------------------------------------------------- */

export type MicCheckStatus =
  | "idle"
  | "requesting"
  | "granted"
  | "denied"
  | "unsupported";

/** Status line under the device check. */
export const MIC_STATUS_MESSAGE: Record<MicCheckStatus, string> = {
  idle: "We'll ask for your microphone when you start",
  requesting: "Waiting for permission…",
  granted: "Microphone works",
  denied:
    "Microphone is blocked. Allow it in your browser's site settings, then reload.",
  unsupported: "This browser can't capture audio. Try Chrome, Safari or Firefox.",
};

/**
 * The per-browser "how to unblock" sentence of §5.1. Sniffing the UA string
 * is the only way to tell the visitor where the setting lives; it only
 * affects copy, never behaviour.
 */
export function micPermissionHint(userAgent: string): string {
  const ua = userAgent.toLowerCase();
  const isIos =
    /iphone|ipad|ipod/.test(ua) ||
    (/macintosh/.test(ua) && /mobile/.test(ua));
  if (isIos) return "On iPhone and iPad: Settings › Safari › Microphone.";
  if (/edg\//.test(ua)) return "In Edge: the lock icon in the address bar › Permissions for this site.";
  if (/chrome\/|chromium|crios/.test(ua)) {
    return "In Chrome: the lock icon in the address bar › Site settings › Microphone.";
  }
  if (/firefox|fxios/.test(ua)) {
    return "In Firefox: the microphone icon in the address bar › Allow.";
  }
  if (/safari/.test(ua)) {
    return "In Safari: Safari › Settings for This Website › Microphone.";
  }
  return "Allow the microphone for this site in your browser's settings.";
}
